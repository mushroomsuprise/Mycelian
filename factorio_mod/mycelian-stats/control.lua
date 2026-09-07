-- Mycelian Stats: write a JSON snapshot for the Mycelian overlay.
-- Read-only. Failures are written as {"error": ...} so a bad prototype never breaks the save.

local MOD_VERSION = "1.0.4"
local TICKS_PER_SECOND = 60
local MAX_FLOW_ROWS = 80
local MAX_POWER_ROWS = 16
local GRAPH_ROWS = 12
local SERIES_POINTS = 60
local FLOW_WINDOW_SAMPLES = 300
local SAMPLE_STEP = 5
local NETWORK_REFRESH_TICKS = 600
local STATS_PATH = "mycelian/stats.json"

local PRECISIONS = {
  "five_seconds",
  "one_minute",
  "ten_minutes",
  "one_hour",
  "ten_hours",
  "fifty_hours",
  "two_hundred_fifty_hours",
  "one_thousand_hours",
}

local nth_interval = nil

local function safe(fn, fallback)
  local ok, result = pcall(fn)
  if ok then
    return result
  end
  return fallback
end

local function is_science_pack(name)
  if type(name) ~= "string" then
    return false
  end
  return string.sub(name, -13) == "-science-pack"
end

local function format_playtime(ticks)
  local sec = math.floor((tonumber(ticks) or 0) / TICKS_PER_SECOND)
  local s = sec % 60
  local m = math.floor(sec / 60) % 60
  local h = math.floor(sec / 3600)
  if h >= 24 then
    local d = math.floor(h / 24)
    h = h % 24
    return string.format("%dd %d:%02d:%02d", d, h, m, s)
  end
  return string.format("%d:%02d:%02d", h, m, s)
end

local function player_force()
  return game.forces["player"] or game.forces.player
end

local function player_surface()
  for _, player in pairs(game.connected_players) do
    local surf = player.physical_surface
    if surf and surf.valid then
      return surf
    end
  end
  return game.surfaces["nauvis"] or game.surfaces[1]
end

local function define_name(define_table, value)
  if type(value) == "string" then
    return value
  end
  if type(define_table) ~= "table" or value == nil then
    return nil
  end
  for name, enumerated in pairs(define_table) do
    if enumerated == value then
      return name
    end
  end
  return nil
end

local PLATFORM_STATE_LABELS = {
  on_the_path = "In transit",
  waiting_at_station = "Docked",
  waiting_for_departure = "Departing",
  no_schedule = "No schedule",
  no_path = "No path",
  paused = "Paused",
  waiting_for_starter_pack = "Waiting for pack",
  starter_pack_on_the_way = "Pack inbound",
  starter_pack_requested = "Pack requested",
}

local function seed_visited_from_world()
  storage.visited = storage.visited or {}
  pcall(function()
    for name, planet in pairs(game.planets or {}) do
      if planet and planet.valid then
        local surf = planet.surface
        if surf and surf.valid then
          storage.visited[name] = true
        end
      end
    end
  end)
end

local function mark_visited(surf)
  if not surf or not surf.valid then
    return
  end
  storage.visited = storage.visited or {}
  local planet = surf.planet
  if planet and planet.valid and planet.name then
    storage.visited[planet.name] = true
  end
end

local function visited_list()
  local out = {}
  for name, flag in pairs(storage.visited or {}) do
    if flag then
      table.insert(out, name)
    end
  end
  table.sort(out)
  return out
end

local function current_location_name(surf)
  if not surf or not surf.valid then
    return nil
  end
  local planet = surf.planet
  if planet and planet.valid and planet.name then
    return planet.name
  end
  local platform = surf.platform
  if platform and platform.valid then
    return "platform:" .. tostring(platform.name or surf.name)
  end
  return surf.name
end

local function flow_rates(stats, name, produced_cat, consumed_cat)
  local rates = {}
  for _, key in ipairs(PRECISIONS) do
    local idx = defines.flow_precision_index[key]
    local produced = 0
    local consumed = 0
    if idx then
      local ok_p, pval = pcall(function()
        return stats.get_flow_count({
          name = name,
          category = produced_cat,
          precision_index = idx,
        })
      end)
      local ok_c, cval = pcall(function()
        return stats.get_flow_count({
          name = name,
          category = consumed_cat,
          precision_index = idx,
        })
      end)
      if ok_p and type(pval) == "number" then
        produced = pval
      end
      if ok_c and type(cval) == "number" then
        consumed = cval
      end
    end
    rates[key] = { produced = produced, consumed = consumed }
  end
  return rates
end

local function flow_series(stats, name, category, precision_key, scale)
  local series = {}
  local idx = defines.flow_precision_index[precision_key]
  if not stats or not idx or type(name) ~= "string" then
    return series
  end
  scale = scale or 1
  -- sample_index 1 is newest. Oldest-first so overlay X runs left→right toward now.
  for i = SERIES_POINTS, 1, -1 do
    local sample = (i - 1) * SAMPLE_STEP + 1
    if sample > FLOW_WINDOW_SAMPLES then
      sample = FLOW_WINDOW_SAMPLES
    end
    local ok, value = pcall(function()
      return stats.get_flow_count({
        name = name,
        category = category,
        precision_index = idx,
        sample_index = sample,
      })
    end)
    local n = 0
    if ok and type(value) == "number" then
      n = value * scale
    end
    table.insert(series, n)
  end
  return series
end

local function attach_item_series(stats, rows, produced_cat, consumed_cat)
  if not stats then
    return
  end
  for i, row in ipairs(rows) do
    if i <= GRAPH_ROWS or is_science_pack(row.name) then
      local series = {}
      for _, key in ipairs(PRECISIONS) do
        series[key] = {
          produced = flow_series(stats, row.name, produced_cat, key, 1),
          consumed = flow_series(stats, row.name, consumed_cat, key, 1),
        }
      end
      row.series = series
    end
  end
end

local function attach_electric_series(stats, rows, category)
  if not stats then
    return
  end
  for i, row in ipairs(rows) do
    if i > GRAPH_ROWS then
      break
    end
    local series = {}
    for _, key in ipairs(PRECISIONS) do
      series[key] = flow_series(stats, row.name, category, key, TICKS_PER_SECOND)
    end
    row.series = series
  end
end

local function sum_named_series(rows)
  local out = {}
  for _, key in ipairs(PRECISIONS) do
    local acc = {}
    for i = 1, SERIES_POINTS do
      acc[i] = 0
    end
    local any = false
    for _, row in ipairs(rows) do
      local s = row.series and row.series[key]
      if type(s) == "table" then
        any = true
        for i, v in ipairs(s) do
          acc[i] = acc[i] + (tonumber(v) or 0)
        end
      end
    end
    if any then
      out[key] = acc
    end
  end
  return out
end

local function collect_flow(stats, produced_cat, consumed_cat, extra_names)
  if not stats then
    return {}
  end
  local produced_counts = safe(function()
    return stats.input_counts
  end, {}) or {}
  local consumed_counts = safe(function()
    return stats.output_counts
  end, {}) or {}

  -- Item/fluid GUI: input = production (left), output = consumption (right).
  if produced_cat == "input" then
    produced_counts = safe(function()
      return stats.input_counts
    end, {}) or {}
    consumed_counts = safe(function()
      return stats.output_counts
    end, {}) or {}
  end

  local names = {}
  local seen = {}
  local function add_name(name)
    if type(name) == "string" and name ~= "" and not seen[name] then
      seen[name] = true
      table.insert(names, name)
    end
  end
  for name, _ in pairs(produced_counts) do
    add_name(name)
  end
  for name, _ in pairs(consumed_counts) do
    add_name(name)
  end
  if extra_names then
    for _, name in ipairs(extra_names) do
      add_name(name)
    end
  end

  local rows = {}
  for _, name in ipairs(names) do
    local rates = flow_rates(stats, name, produced_cat, consumed_cat)
    local minute = rates.one_minute or { produced = 0, consumed = 0 }
    local produced_total = tonumber(produced_counts[name]) or 0
    local consumed_total = tonumber(consumed_counts[name]) or 0
    table.insert(rows, {
      name = name,
      produced = produced_total,
      consumed = consumed_total,
      rates = rates,
      _sort = math.max(minute.produced or 0, minute.consumed or 0),
    })
  end
  table.sort(rows, function(a, b)
    if a._sort == b._sort then
      return a.name < b.name
    end
    return a._sort > b._sort
  end)

  local kept = {}
  local kept_count = 0
  for _, row in ipairs(rows) do
    local keep = kept_count < MAX_FLOW_ROWS
      or is_science_pack(row.name)
      or (row._sort or 0) > 0
    if keep and kept_count < MAX_FLOW_ROWS + 16 then
      row._sort = nil
      table.insert(kept, row)
      kept_count = kept_count + 1
    end
  end
  attach_item_series(stats, kept, produced_cat, consumed_cat)
  return kept
end

local function entity_count_on_surface(surface, name)
  if not surface or not surface.valid or type(name) ~= "string" then
    return 0
  end
  local ok, count = pcall(function()
    return surface.count_entities_filtered({ name = name })
  end)
  if ok and type(count) == "number" then
    return count
  end
  return 0
end

local function proto_max_energy_production(name)
  local proto = prototypes.entity[name]
  if not proto then
    return 0
  end
  local ok, value = pcall(function()
    return proto.get_max_energy_production()
  end)
  if ok and type(value) == "number" then
    return value
  end
  return 0
end

local function proto_buffer_capacity(name)
  local proto = prototypes.entity[name]
  if not proto then
    return 0
  end
  local src = proto.electric_energy_source_prototype
  if src and type(src.buffer_capacity) == "number" then
    return src.buffer_capacity
  end
  return 0
end

local function collect_electric_side(stats, surface, category)
  local counts
  if category == "output" then
    counts = safe(function()
      return stats.output_counts
    end, {}) or {}
  else
    counts = safe(function()
      return stats.input_counts
    end, {}) or {}
  end
  local rows = {}
  for name, _ in pairs(counts) do
    local per_tick = 0
    local ok, value = pcall(function()
      return stats.get_flow_count({
        name = name,
        category = category,
        precision_index = defines.flow_precision_index.five_seconds,
      })
    end)
    if ok and type(value) == "number" then
      per_tick = value
    end
    table.insert(rows, {
      name = name,
      watts = per_tick * TICKS_PER_SECOND,
      count = 0,
      _sort = per_tick,
    })
  end
  table.sort(rows, function(a, b)
    return a._sort > b._sort
  end)
  local out = {}
  for i, row in ipairs(rows) do
    if i > MAX_POWER_ROWS then
      break
    end
    row.count = entity_count_on_surface(surface, row.name)
    row._sort = nil
    table.insert(out, row)
  end
  attach_electric_series(stats, out, category)
  return out
end

local function pick_power_network(surface)
  if not surface or not surface.valid then
    return nil, nil
  end
  local poles = safe(function()
    return surface.find_entities_filtered({ type = "electric-pole", limit = 250 })
  end, {}) or {}
  local best_pole, best_id, best_score = nil, nil, -1
  local seen = {}
  for _, pole in pairs(poles) do
    if pole.valid then
      local id = pole.electric_network_id
      if id and not seen[id] then
        seen[id] = true
        local stats = pole.electric_network_statistics
        local score = 0
        if stats then
          local inputs = safe(function()
            return stats.input_counts
          end, {}) or {}
          for _, count in pairs(inputs) do
            score = score + (tonumber(count) or 0)
          end
        end
        if score > best_score then
          best_score = score
          best_pole = pole
          best_id = id
        end
      end
    end
  end
  return best_pole, best_id
end

local function network_state(surface)
  storage.networks = storage.networks or {}
  local key = (surface and surface.name) or "_"
  if not storage.networks[key] then
    storage.networks[key] = {}
  end
  return storage.networks[key]
end

local function refresh_network(surface)
  local slot = network_state(surface)
  local pole, id = pick_power_network(surface)
  slot.surface = surface and surface.name or nil
  slot.id = id
  if pole and pole.valid then
    slot.unit_number = pole.unit_number
  else
    slot.unit_number = nil
  end
  slot.refreshed_tick = game.tick
end

local function cached_network_pole(surface)
  local slot = network_state(surface)
  local due = (slot.refreshed_tick or 0) + NETWORK_REFRESH_TICKS
  if not slot.unit_number or game.tick >= due then
    refresh_network(surface)
    slot = network_state(surface)
  end
  if not slot.unit_number or not surface or not surface.valid then
    return nil, slot.id
  end
  local pole = safe(function()
    return game.get_entity_by_unit_number(slot.unit_number)
  end, nil)
  if pole and pole.valid then
    return pole, pole.electric_network_id or slot.id
  end
  local poles = safe(function()
    return surface.find_entities_filtered({ type = "electric-pole", limit = 250 })
  end, {}) or {}
  for _, candidate in pairs(poles) do
    if candidate.valid and candidate.unit_number == slot.unit_number then
      return candidate, candidate.electric_network_id or slot.id
    end
  end
  refresh_network(surface)
  slot = network_state(surface)
  return nil, slot.id
end

local function collect_power(surface)
  local pole, network_id = cached_network_pole(surface)
  if not pole or not pole.valid then
    return {
      network_id = network_id,
      surface = surface and surface.name or nil,
      generation_w = 0,
      capacity_w = 0,
      consumption_w = 0,
      satisfaction = 1,
      accumulator_j = 0,
      accumulator_capacity_j = 0,
      producers = {},
      consumers = {},
      series = {},
    }
  end
  local stats = pole.electric_network_statistics
  local producers = collect_electric_side(stats, surface, "output")
  local consumers = collect_electric_side(stats, surface, "input")
  local generation_w = 0
  local consumption_w = 0
  local capacity_w = 0
  for _, row in ipairs(producers) do
    generation_w = generation_w + (row.watts or 0)
    capacity_w = capacity_w
      + proto_max_energy_production(row.name) * (row.count or 0) * TICKS_PER_SECOND
  end
  for _, row in ipairs(consumers) do
    consumption_w = consumption_w + (row.watts or 0)
  end
  local acc_j = 0
  local acc_cap = 0
  local storage_counts = safe(function()
    return stats.storage_counts
  end, {}) or {}
  for name, value in pairs(storage_counts) do
    acc_j = acc_j + (tonumber(value) or 0)
    acc_cap = acc_cap + proto_buffer_capacity(name) * entity_count_on_surface(surface, name)
  end
  local satisfaction = 1
  if consumption_w > 0 then
    satisfaction = math.max(0, math.min(1, generation_w / consumption_w))
  end
  if capacity_w < generation_w then
    capacity_w = generation_w
  end
  local gen_series = sum_named_series(producers)
  local cons_series = sum_named_series(consumers)
  local power_series = {}
  for _, key in ipairs(PRECISIONS) do
    if gen_series[key] or cons_series[key] then
      power_series[key] = {
        generation = gen_series[key] or {},
        consumption = cons_series[key] or {},
      }
    end
  end
  return {
    network_id = network_id,
    surface = surface and surface.name or nil,
    generation_w = generation_w,
    capacity_w = capacity_w,
    consumption_w = consumption_w,
    satisfaction = satisfaction,
    accumulator_j = acc_j,
    accumulator_capacity_j = acc_cap,
    producers = producers,
    consumers = consumers,
    series = power_series,
  }
end

local function collect_research(force)
  local research = nil
  local tech = force.current_research
  if tech and tech.valid then
    local progress = tonumber(force.research_progress) or 0
    local units = tonumber(tech.research_unit_count) or 0
    local unit_energy = tonumber(tech.research_unit_energy) or 0
    local remaining = math.max(0, 1 - progress) * units
    local eta = nil
    storage.research_eta = storage.research_eta or {}
    local prev = storage.research_eta
    if prev.name == tech.name and prev.tick and prev.progress and progress > prev.progress then
      local d_progress = progress - prev.progress
      local d_tick = game.tick - prev.tick
      if d_progress > 0 and d_tick > 0 then
        eta = ((1 - progress) / d_progress) * d_tick / TICKS_PER_SECOND
      end
    elseif remaining > 0 and unit_energy > 0 then
      eta = remaining * unit_energy / TICKS_PER_SECOND
    end
    storage.research_eta = {
      name = tech.name,
      progress = progress,
      tick = game.tick,
    }
    research = {
      name = tech.name,
      localised = tech.name,
      progress = progress,
      level = tech.level,
      eta_seconds = eta,
    }
  else
    storage.research_eta = {}
  end
  local queue_length = 0
  pcall(function()
    queue_length = #(force.research_queue or {})
  end)
  return research, queue_length
end

local function collect_science(item_rows)
  local packs = {}
  local spm = 0
  for _, row in ipairs(item_rows) do
    if is_science_pack(row.name) then
      local per_min = 0
      if row.rates and row.rates.one_minute then
        per_min = tonumber(row.rates.one_minute.produced) or 0
      end
      local pack = { name = row.name, per_min = per_min }
      if type(row.series) == "table" then
        local pack_series = {}
        for key, block in pairs(row.series) do
          if type(block) == "table" and type(block.produced) == "table" then
            pack_series[key] = block.produced
          end
        end
        pack.series = pack_series
      end
      table.insert(packs, pack)
      spm = spm + per_min
    end
  end
  table.sort(packs, function(a, b)
    if a.per_min == b.per_min then
      return a.name < b.name
    end
    return a.per_min > b.per_min
  end)
  local spm_series = {}
  for _, key in ipairs(PRECISIONS) do
    local acc = {}
    for i = 1, SERIES_POINTS do
      acc[i] = 0
    end
    local any = false
    for _, pack in ipairs(packs) do
      local s = pack.series and pack.series[key]
      if type(s) == "table" then
        any = true
        for i, v in ipairs(s) do
          acc[i] = acc[i] + (tonumber(v) or 0)
        end
      end
    end
    if any then
      spm_series[key] = acc
    end
  end
  return spm, packs, spm_series
end

local function collect_platforms(force)
  local platforms = {}
  pcall(function()
    for _, plat in pairs(force.platforms or {}) do
      if plat and plat.valid then
        local location = nil
        pcall(function()
          local loc = plat.space_location
          if loc then
            location = loc.name or nil
          end
        end)
        if location then
          storage.visited = storage.visited or {}
          storage.visited[location] = true
        end
        local origin = nil
        pcall(function()
          local last = plat.last_visited_space_location
          if last then
            origin = last.name
          end
        end)
        local destination = nil
        pcall(function()
          local sched = plat.schedule
          if sched and sched.records and sched.current then
            local rec = sched.records[sched.current]
            if rec then
              destination = rec.station
              if type(destination) == "table" then
                destination = destination.name
              end
            end
          end
        end)
        local state_name = define_name(defines.space_platform_state, plat.state)
        local in_transit = state_name == "on_the_path"
        pcall(function()
          if plat.space_connection then
            in_transit = true
          end
        end)
        local label
        if location and not in_transit then
          label = location
        elseif in_transit then
          if origin and destination and origin ~= destination then
            label = tostring(origin) .. " -> " .. tostring(destination)
          elseif destination then
            label = destination
          elseif origin then
            label = origin
          else
            label = origin or destination or ""
          end
        else
          label = location or PLATFORM_STATE_LABELS[state_name or ""] or state_name or "unknown"
        end
        table.insert(platforms, {
          name = plat.name,
          state = state_name or tostring(plat.state),
          location = location,
          origin = origin,
          destination = destination,
          in_transit = in_transit,
          label = label,
          weight = plat.weight,
          speed = plat.speed,
        })
      end
    end
  end)
  return platforms
end

local function collect_surface_economy(force, surface, export_fluids)
  local items = {}
  local fluids = {}
  if force and surface and surface.valid then
    local item_stats = safe(function()
      return force.get_item_production_statistics(surface)
    end, nil)
    items = collect_flow(item_stats, "input", "output", nil)
    if export_fluids then
      local fluid_stats = safe(function()
        return force.get_fluid_production_statistics(surface)
      end, nil)
      if fluid_stats then
        fluids = collect_flow(fluid_stats, "input", "output", nil)
      end
    end
  end
  return {
    power = collect_power(surface),
    items = items,
    fluids = fluids,
  }
end

local function surface_for_planet(name)
  if type(name) ~= "string" or name == "" then
    return nil
  end
  local surf = nil
  pcall(function()
    local planet = game.planets and game.planets[name]
    if planet and planet.valid then
      surf = planet.surface
    end
  end)
  if surf and surf.valid then
    return surf
  end
  local named = game.surfaces[name]
  if named and named.valid then
    return named
  end
  return nil
end

local function collect_by_surface(force, current, export_fluids)
  local by_surface = {}
  local seen = {}
  local function add(surf, with_fluids)
    if not surf or not surf.valid then
      return
    end
    local key = surf.name
    if seen[key] then
      return
    end
    seen[key] = true
    by_surface[key] = collect_surface_economy(force, surf, with_fluids)
  end
  add(current, export_fluids)
  for _, name in ipairs(visited_list()) do
    add(surface_for_planet(name), export_fluids)
  end
  return by_surface
end

local function collect_misc(force, surface)
  local rockets = safe(function()
    return force.rockets_launched
  end, 0) or 0
  local evolution = safe(function()
    if surface then
      return force.get_evolution_factor(surface)
    end
    return force.get_evolution_factor()
  end, 0) or 0
  local pollution = safe(function()
    return surface.get_total_pollution()
  end, 0) or 0
  local kills = 0
  pcall(function()
    local stats = force.get_kill_count_statistics(surface)
    local produced = stats.input_counts or {}
    for _, count in pairs(produced) do
      kills = kills + (tonumber(count) or 0)
    end
  end)
  return {
    rockets_launched = rockets,
    evolution = evolution,
    pollution = pollution,
    kills = kills,
  }
end

local function build_payload()
  local force = player_force()
  local surface = player_surface()
  seed_visited_from_world()
  mark_visited(surface)

  local export_fluids = false
  pcall(function()
    export_fluids = settings.global["mycelian-export-fluids"].value and true or false
  end)

  local by_surface = collect_by_surface(force, surface, export_fluids)
  local current_block = (surface and by_surface[surface.name]) or {
    power = collect_power(surface),
    items = {},
    fluids = {},
  }
  local items = current_block.items or {}
  local fluids = current_block.fluids or {}

  local spm, packs, spm_series = collect_science(items)
  local research, queue_length = nil, 0
  if force then
    research, queue_length = collect_research(force)
  end

  local space_age = script.active_mods["space-age"] ~= nil
  local platforms = {}
  if force then
    platforms = collect_platforms(force)
  end

  return {
    v = 1,
    tick = game.tick,
    ticks_played = game.ticks_played,
    game_version = helpers.game_version,
    mod_version = MOD_VERSION,
    playtime_text = format_playtime(game.ticks_played),
    speed = game.speed,
    surface = surface and surface.name or nil,
    space_age = space_age,
    power = current_block.power or collect_power(surface),
    production = {
      precision = "one_minute",
      items = items,
      fluids = fluids,
    },
    by_surface = by_surface,
    science = {
      spm = spm,
      packs = packs,
      series = spm_series or {},
      research = research,
      queue_length = queue_length,
    },
    planets = {
      current = current_location_name(surface),
      visited = visited_list(),
      platforms = platforms,
    },
    misc = (force and collect_misc(force, surface)) or {
      rockets_launched = 0,
      evolution = 0,
      pollution = 0,
      kills = 0,
    },
  }
end

local function write_error(message)
  local payload = helpers.table_to_json({
    v = 1,
    tick = (game and game.tick) or 0,
    mod_version = MOD_VERSION,
    error = tostring(message),
  })
  helpers.write_file(STATS_PATH, payload, false)
end

local function export_stats()
  local ok, payload = pcall(build_payload)
  if not ok then
    write_error(payload)
    log("mycelian-stats export failed: " .. tostring(payload))
    return
  end
  local ok_json, json = pcall(function()
    return helpers.table_to_json(payload)
  end)
  if not ok_json then
    write_error(json)
    log("mycelian-stats json failed: " .. tostring(json))
    return
  end
  local ok_write, err = pcall(function()
    helpers.write_file(STATS_PATH, json, false)
  end)
  if not ok_write then
    log("mycelian-stats write failed: " .. tostring(err))
  end
end

local function register_nth()
  local interval = 60
  pcall(function()
    interval = settings.global["mycelian-export-interval"].value
  end)
  if type(interval) ~= "number" or interval < 15 then
    interval = 60
  end
  if nth_interval then
    script.on_nth_tick(nth_interval, nil)
  end
  nth_interval = interval
  script.on_nth_tick(nth_interval, export_stats)
end

script.on_init(function()
  storage.visited = {}
  storage.network = {}
  storage.networks = {}
  storage.research_eta = {}
  for _, player in pairs(game.players) do
    mark_visited(player.physical_surface)
  end
  register_nth()
end)

script.on_load(function()
  register_nth()
end)

script.on_configuration_changed(function()
  storage.visited = storage.visited or {}
  storage.network = storage.network or {}
  storage.networks = storage.networks or {}
  register_nth()
end)

script.on_event(defines.events.on_runtime_mod_setting_changed, function(event)
  if event.setting == "mycelian-export-interval" then
    register_nth()
  end
end)

script.on_event(defines.events.on_player_changed_surface, function(event)
  local player = game.get_player(event.player_index)
  if player then
    mark_visited(player.physical_surface)
  end
end)

script.on_event(defines.events.on_player_joined_game, function(event)
  local player = game.get_player(event.player_index)
  if player then
    mark_visited(player.physical_surface)
  end
end)
