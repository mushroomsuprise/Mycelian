data:extend({
  {
    type = "int-setting",
    name = "mycelian-export-interval",
    setting_type = "runtime-global",
    default_value = 60,
    minimum_value = 15,
    maximum_value = 3600,
    order = "a",
  },
  {
    type = "bool-setting",
    name = "mycelian-export-fluids",
    setting_type = "runtime-global",
    default_value = true,
    order = "b",
  },
})
