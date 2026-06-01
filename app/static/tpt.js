/**
 * Tenera Persona Tracking browser snippet.
 *
 * Install with:
 * <script src="https://<your-tpt-host>/tpt.js" data-write-key="..." async></script>
 */
(function (w, d) {
  'use strict';

  var RECORDER_KEY = '__TPT_RECORDER_ACTIVE__';
  var tag = d.currentScript || d.querySelector('script[data-write-key]');
  var scriptOrigin = tag && tag.src ? new URL(tag.src, w.location.href).origin : w.location.origin;
  var TPT_URL = (tag && tag.getAttribute('data-url')) || scriptOrigin;
  var WRITE_KEY = tag && tag.getAttribute('data-write-key');
  var PROJECT_ID = tag && tag.getAttribute('data-project-id');
  var PROJECT_NAME = tag && tag.getAttribute('data-project-name');
  var ORG_NAME = tag && tag.getAttribute('data-organization-name');
  var ORG_DOMAIN = tag && tag.getAttribute('data-organization-domain');

  if (!WRITE_KEY) return;
  if (w[RECORDER_KEY]) {
    w.__TPT_DUPLICATE_RECORDER__ = true;
    return;
  }
  w[RECORDER_KEY] = true;

  var APP_ENV =
    w.location.hostname === 'localhost' || w.location.hostname.startsWith('staging.')
      ? 'staging'
      : 'production';
  var FLUSH_INTERVAL_MS = 5000;
  var FIRST_FLUSH_MS = 800;

  function uuid() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function getDistinctId() {
    try {
      var key = '_tpt_did';
      var id = localStorage.getItem(key);
      if (!id) { id = uuid(); localStorage.setItem(key, id); }
      return id;
    } catch (e) { return uuid(); }
  }

  var distinctId = getDistinctId();
  var sessionId = null;
  var lastPath = null;
  var pageEntryTime = 0;
  var eventBuffer = [];
  var flushTimer = null;
  var firstFlushTimer = null;
  var stopRecording = null;
  var pathnameRef = w.location.pathname;

  function projectHeaders() {
    var headers = { 'Content-Type': 'application/json', 'X-API-Key': WRITE_KEY };
    if (PROJECT_ID) headers['X-TPT-Project-Id'] = PROJECT_ID;
    if (PROJECT_NAME) headers['X-TPT-Project-Name'] = PROJECT_NAME;
    if (ORG_NAME) headers['X-TPT-Organization-Name'] = ORG_NAME;
    if (ORG_DOMAIN) headers['X-TPT-Organization-Domain'] = ORG_DOMAIN;
    return headers;
  }

  function stampProjectProperties(properties) {
    var stamped = Object.assign({}, properties);
    if (PROJECT_ID) {
      stamped.project_id = PROJECT_ID;
      stamped.teneraProjectId = PROJECT_ID;
    }
    if (PROJECT_NAME) stamped.project_name = PROJECT_NAME;
    if (ORG_NAME) stamped.organization_name = ORG_NAME;
    if (ORG_DOMAIN) stamped.organization_domain = ORG_DOMAIN;
    return stamped;
  }

  function post(path, body, keepalive) {
    return fetch(TPT_URL + path, {
      method: 'POST',
      headers: projectHeaders(),
      body: JSON.stringify(body),
      keepalive: Boolean(keepalive),
    });
  }

  function tptTrack(eventType, props) {
    try {
      post('/api/v1/track?distinct_id=' + encodeURIComponent(distinctId), {
        event_type: eventType,
        properties: stampProjectProperties(Object.assign({ session_id: sessionId, env: APP_ENV }, props)),
      }, true).catch(function () {});
    } catch (e) {}
  }

  function sessionEventsPath() {
    return '/api/v1/sessions/' + sessionId + '/events' +
      (PROJECT_ID ? '?project_id=' + encodeURIComponent(PROJECT_ID) : '');
  }

  function flushReplayEvents(useKeepalive) {
    var batch = eventBuffer.splice(0);
    if (!batch.length || !sessionId) return;
    post(sessionEventsPath(), batch, useKeepalive).catch(function () {
      if (!useKeepalive) eventBuffer = batch.concat(eventBuffer);
    });
  }

  function startRecording() {
    if (!w.rrweb || !sessionId) return;
    stopRecording = w.rrweb.record({
      emit: function (event) { eventBuffer.push(event); },
      maskAllInputs: true,
      blockClass: 'ph-no-capture',
      maskTextClass: 'ph-mask',
      ignoreClass: 'ph-ignore-input',
    }) || null;
    firstFlushTimer = setTimeout(function () { flushReplayEvents(false); }, FIRST_FLUSH_MS);
    flushTimer = setInterval(function () { flushReplayEvents(false); }, FLUSH_INTERVAL_MS);
  }

  function loadRrweb() {
    var s = d.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/rrweb@latest/dist/rrweb.min.js';
    s.async = true;
    s.onload = startRecording;
    s.onerror = function () { w.__TPT_RRWEB_LOAD_FAILED__ = true; };
    d.head.appendChild(s);
  }

  function startSession() {
    post('/api/v1/sessions', {
      distinct_id: distinctId,
      url: w.location.href,
      env: APP_ENV,
      project_id: PROJECT_ID || undefined,
      project_name: PROJECT_NAME || undefined,
      organization_name: ORG_NAME || undefined,
      organization_domain: ORG_DOMAIN || undefined,
    }, false)
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data || !data.id) return;
        sessionId = data.id;
        pathnameRef = w.location.pathname;
        lastPath = pathnameRef;
        pageEntryTime = Date.now();
        tptTrack('page_view', { page: pathnameRef, url: w.location.href });
        loadRrweb();
        d.addEventListener('click', handleClick, true);
        d.addEventListener('submit', handleSubmit, true);
      })
      .catch(function () {});
  }

  function handleClick(e) {
    if (!sessionId) return;
    var el = e.target;
    var interactive = el.closest
      ? el.closest('button, a[href], [role="button"], input[type="submit"], input[type="button"]')
      : null;
    if (!interactive) return;
    var tagName = interactive.tagName.toLowerCase();
    var text = (interactive.textContent || interactive.getAttribute('aria-label') || '')
      .trim().replace(/\s+/g, ' ').slice(0, 100) || undefined;
    var href = tagName === 'a' ? interactive.href : undefined;
    tptTrack('click', { text: text, href: href, element: tagName, page: pathnameRef });
  }

  function handleSubmit(e) {
    if (!sessionId) return;
    var fields = [];
    var els = e.target.elements || [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.name && el.type !== 'password' && el.type !== 'hidden') fields.push(el.name);
    }
    tptTrack('form_submit', {
      form_id: e.target.id || e.target.name || undefined,
      fields: fields.length ? fields : undefined,
      page: pathnameRef,
    });
  }

  function onNavigate(newPath) {
    if (newPath === lastPath || !sessionId) return;
    if (lastPath && pageEntryTime > 0) {
      var dur = Math.round((Date.now() - pageEntryTime) / 1000);
      if (dur >= 3) tptTrack('time_on_page', { page: lastPath, duration_seconds: dur });
    }
    lastPath = newPath;
    pathnameRef = newPath;
    pageEntryTime = Date.now();
    tptTrack('page_view', { page: newPath, url: w.location.href });
  }

  ['pushState', 'replaceState'].forEach(function (fn) {
    var orig = history[fn];
    history[fn] = function () {
      orig.apply(history, arguments);
      onNavigate(w.location.pathname);
    };
  });
  w.addEventListener('popstate', function () { onNavigate(w.location.pathname); });

  var queue = (w.tenera && w.tenera.q) || [];
  w.tenera = function () {
    var args = Array.prototype.slice.call(arguments);
    if (args[0] === 'identify') {
      distinctId = args[1] || distinctId;
    } else if (args[0] === 'track') {
      tptTrack(args[1], args[2] || {});
    }
  };
  for (var i = 0; i < queue.length; i++) w.tenera.apply(null, queue[i]);

  w.addEventListener('pagehide', function () {
    if (lastPath && pageEntryTime > 0) {
      var dur = Math.round((Date.now() - pageEntryTime) / 1000);
      if (dur >= 3) tptTrack('time_on_page', { page: lastPath, duration_seconds: dur });
    }
    flushReplayEvents(true);
    if (stopRecording) stopRecording();
    if (firstFlushTimer) clearTimeout(firstFlushTimer);
    if (flushTimer) clearInterval(flushTimer);
    w[RECORDER_KEY] = false;
  });

  if (d.readyState === 'loading') d.addEventListener('DOMContentLoaded', startSession);
  else startSession();
})(window, document);
