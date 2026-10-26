/*
 * Mycelian media preview: play / stop buttons for sounds, random sound folders and
 * text-to-speech in the app (Alerts tab). Loaded once per page by modules/uiwindows/media_preview.py.
 *
 * A button carries what to play in data attributes, so a click plays straight away
 * (inside the click, which browsers require for sound and speech):
 *   data-pv="sound"   data-src (URL)              data-vol (0-100)
 *   data-pv="random"  data-base (folder URL)      data-files (JSON list of names)  data-vol
 *   data-pv="tts"     data-texts (JSON list)      data-rate / data-pitch / data-tts-vol
 * toggle() returns the file a random button picked, so the click handler can emit it to Python.
 * Clicking the playing button again stops it; starting another preview stops the last one.
 */
(function () {
  if (window.MycelianPreview) { return; }

  var audio = null;
  var activeBtn = null;
  var speaking = false;

  function iconOf(btn) { return btn ? btn.querySelector('.q-icon, .material-icons') : null; }

  function setPlaying(btn, on) {
    if (!btn) { return; }
    btn.classList.toggle('is-playing', on);
    var icon = iconOf(btn);
    if (icon) { icon.textContent = on ? 'stop' : (btn.getAttribute('data-icon') || 'play_arrow'); }
  }

  function stop() {
    if (audio) {
      try { audio.pause(); } catch (e) { /* already stopped */ }
      audio.src = '';
      audio = null;
    }
    if (speaking && window.speechSynthesis) {
      try { window.speechSynthesis.cancel(); } catch (e) { /* nothing to cancel */ }
    }
    speaking = false;
    setPlaying(activeBtn, false);
    activeBtn = null;
  }

  function volumeOf(btn) {
    var v = parseFloat(btn.getAttribute('data-vol'));
    return isFinite(v) ? Math.max(0, Math.min(1, v / 100)) : 1;
  }

  function playUrl(btn, url) {
    if (!url) { return false; }
    var a = new Audio();
    a.preload = 'auto';
    a.volume = volumeOf(btn);
    a.onended = function () { if (audio === a) { stop(); } };
    a.onerror = function () { if (audio === a) { stop(); } };
    a.src = url;
    audio = a;
    activeBtn = btn;
    setPlaying(btn, true);
    var p = a.play();
    if (p && p.catch) { p.catch(function () { if (audio === a) { stop(); } }); }
    return true;
  }

  function parseList(raw) {
    try {
      var list = JSON.parse(raw || '[]');
      return Array.isArray(list) ? list : [];
    } catch (e) {
      return [];
    }
  }

  function speak(btn) {
    if (!window.speechSynthesis || typeof SpeechSynthesisUtterance === 'undefined') { return false; }
    var texts = parseList(btn.getAttribute('data-texts')).filter(function (t) { return String(t || '').trim(); });
    if (!texts.length) { return false; }
    var u = new SpeechSynthesisUtterance(String(texts[Math.floor(Math.random() * texts.length)]));
    var num = function (name, d) { var v = parseFloat(btn.getAttribute(name)); return isFinite(v) ? v : d; };
    u.rate = num('data-rate', 1);
    u.pitch = num('data-pitch', 1);
    u.volume = Math.max(0, Math.min(1, num('data-tts-vol', 1)));
    u.onend = u.onerror = function () { if (activeBtn === btn && speaking) { speaking = false; setPlaying(btn, false); activeBtn = null; } };
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
    speaking = true;
    activeBtn = btn;
    setPlaying(btn, true);
    return true;
  }

  function toggle(ev) {
    var target = ev && (ev.target || ev.srcElement);
    var btn = target && target.closest ? target.closest('[data-pv]') : null;
    if (!btn) { return null; }
    if (activeBtn === btn) { stop(); return null; }
    stop();
    var mode = btn.getAttribute('data-pv');
    if (mode === 'sound') {
      playUrl(btn, btn.getAttribute('data-src'));
    } else if (mode === 'random') {
      var files = parseList(btn.getAttribute('data-files'));
      if (!files.length) { return null; }
      var name = String(files[Math.floor(Math.random() * files.length)]);
      var base = btn.getAttribute('data-base') || '';
      return playUrl(btn, base + encodeURIComponent(name)) ? name : null;
    } else if (mode === 'tts') {
      speak(btn);
    }
    return null;
  }

  /* Length of an audio file in seconds (null when it can't be read). */
  function duration(url) {
    return new Promise(function (resolve) {
      if (!url) { resolve(null); return; }
      var a = new Audio();
      var done = false;
      var finish = function (value) { if (!done) { done = true; a.src = ''; resolve(value); } };
      a.preload = 'metadata';
      a.onloadedmetadata = function () { finish(isFinite(a.duration) && a.duration > 0 ? a.duration : null); };
      a.onerror = function () { finish(null); };
      setTimeout(function () { finish(null); }, 8000);
      a.src = url;
    });
  }

  /* Volume of the file picker's preview player (0-100); a video unmutes once it is turned up. */
  function setPickerVolume(value) {
    var v = Math.max(0, Math.min(1, (parseFloat(value) || 0) / 100));
    document.querySelectorAll('[data-pv-media]').forEach(function (el) {
      el.setAttribute('data-vol', String(Math.round(v * 100)));
      el.volume = v;
      if (el.tagName === 'VIDEO') { el.muted = v === 0; }
    });
  }

  window.MycelianPreview = { toggle: toggle, stop: stop, duration: duration, setPickerVolume: setPickerVolume };
})();
