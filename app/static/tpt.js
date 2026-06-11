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

  // Campaign / ad-click params recognized for attribution (mirrors posthog-js CAMPAIGN_PARAMS)
  var CAMPAIGN_PARAMS = [
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term',
    'gad_source', 'mc_cid',
    'gclid', 'gclsrc', 'dclid', 'gbraid', 'wbraid', 'fbclid', 'msclkid',
    'twclid', 'li_fat_id', 'igshid', 'ttclid', 'rdt_cid', 'epik', 'qclid',
    'sccid', 'irclid', '_kx',
  ];
  var DIRECT = '$direct';
  var INITIAL_INFO_KEY = '_tpt_initial';

  // Form field names that suggest sensitive content (mirrors posthog-js autocapture)
  var SENSITIVE_FIELD_RE =
    /^cc|cardnum|ccnum|creditcard|csc|cvc|cvv|exp|pass|pwd|routing|seccode|securitycode|securitynum|socialsec|socsec|ssn/i;
  // Values that look like credit card or SSN numbers are never captured
  var CC_VALUE_RE =
    /^(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|6(?:011|5[0-9]{2})[0-9]{12}|3[47][0-9]{13}|3(?:0[0-5]|[68][0-9])[0-9]{11}|(?:2131|1800|35[0-9]{3})[0-9]{11})$/;
  var SSN_VALUE_RE = /^\d{3}-?\d{2}-?\d{4}$/;

  // Rage click heuristic (mirrors posthog-js): 3 clicks, each within 30px
  // (Manhattan distance) and 1000ms of the previous one
  var RAGE_CLICK_COUNT = 3;
  var RAGE_THRESHOLD_PX = 30;
  var RAGE_TIMEOUT_MS = 1000;
  // Pagination-style controls that invite rapid legitimate clicking
  var RAGE_IGNORE_TEXT = ['next', 'previous', 'prev', '>', '<', '+', '-', '−', '–'];

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
  var rageClicks = [];

  function sanitizeText(raw) {
    if (!raw) return undefined;
    var text = raw.trim().replace(/\s+/g, ' ').slice(0, 100);
    if (!text) return undefined;
    if (CC_VALUE_RE.test(text.replace(/[- ]/g, '')) || SSN_VALUE_RE.test(text)) return undefined;
    return text;
  }

  function getCampaignParams(url) {
    var props = {};
    try {
      var search = new URL(url, w.location.href).searchParams;
      for (var i = 0; i < CAMPAIGN_PARAMS.length; i++) {
        var value = search.get(CAMPAIGN_PARAMS[i]);
        if (value) props[CAMPAIGN_PARAMS[i]] = value;
      }
    } catch (e) {}
    return props;
  }

  function referringDomain(referrer) {
    if (!referrer || referrer === DIRECT) return DIRECT;
    try { return new URL(referrer).host || DIRECT; } catch (e) { return DIRECT; }
  }

  // Last-touch attribution: campaign params from the current URL plus referrer
  function getAttribution() {
    var props = getCampaignParams(w.location.href);
    props.referrer = (d.referrer || DIRECT).slice(0, 1000);
    props.referring_domain = referringDomain(d.referrer);
    return props;
  }

  // First-touch attribution: the referrer/URL of this device's very first visit,
  // stored once and expanded to initial_* properties (mirrors PostHog $initial_*).
  // Stored as the raw {r, u} pair so the recognized param list can evolve later.
  function getInitialProps() {
    var info;
    try {
      var stored = localStorage.getItem(INITIAL_INFO_KEY);
      if (!stored) {
        stored = JSON.stringify({
          r: (d.referrer || DIRECT).slice(0, 1000),
          u: w.location.href.slice(0, 1000),
        });
        localStorage.setItem(INITIAL_INFO_KEY, stored);
      }
      info = JSON.parse(stored);
    } catch (e) { return {}; }
    if (!info) return {};
    var props = {
      initial_referrer: info.r,
      initial_referring_domain: referringDomain(info.r),
    };
    if (info.u) {
      props.initial_current_url = info.u;
      try { props.initial_pathname = new URL(info.u).pathname; } catch (e) {}
      var campaign = getCampaignParams(info.u);
      for (var key in campaign) props['initial_' + key] = campaign[key];
    }
    return props;
  }

  function isRageClick(x, y, timestamp) {
    var last = rageClicks[rageClicks.length - 1];
    if (
      last &&
      Math.abs(x - last.x) + Math.abs(y - last.y) < RAGE_THRESHOLD_PX &&
      timestamp - last.timestamp < RAGE_TIMEOUT_MS
    ) {
      rageClicks.push({ x: x, y: y, timestamp: timestamp });
      if (rageClicks.length === RAGE_CLICK_COUNT) return true;
    } else {
      rageClicks = [{ x: x, y: y, timestamp: timestamp }];
    }
    return false;
  }

  function shouldCaptureRageClick(el) {
    if (!el || !el.closest) return true;
    if (el.closest('.ph-no-capture, .ph-no-rageclick')) return false;
    var text = (el.textContent || '').trim().toLowerCase();
    return RAGE_IGNORE_TEXT.indexOf(text) === -1;
  }

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
        tptTrack('page_view', Object.assign(
          { page: pathnameRef, url: w.location.href },
          getAttribution(),
          getInitialProps()
        ));
        loadRrweb();
        d.addEventListener('click', handleClick, true);
        d.addEventListener('submit', handleSubmit, true);
      })
      .catch(function () {});
  }

  function handleClick(e) {
    if (!sessionId) return;
    var el = e.target;
    // Rage clicks fire on any element — clicking dead UI is exactly the signal
    if (
      isRageClick(e.clientX, e.clientY, e.timeStamp || Date.now()) &&
      shouldCaptureRageClick(el)
    ) {
      tptTrack('rage_click', {
        text: el && el.textContent ? sanitizeText(el.textContent) : undefined,
        element: el && el.tagName ? el.tagName.toLowerCase() : undefined,
        page: pathnameRef,
      });
    }
    var interactive = el.closest
      ? el.closest('button, a[href], [role="button"], input[type="submit"], input[type="button"]')
      : null;
    if (!interactive) return;
    if (interactive.closest && interactive.closest('.ph-no-capture')) return;
    var tagName = interactive.tagName.toLowerCase();
    var text = sanitizeText(interactive.textContent || interactive.getAttribute('aria-label') || '');
    var href = tagName === 'a' ? interactive.href : undefined;
    tptTrack('click', { text: text, href: href, element: tagName, page: pathnameRef });
  }

  function handleSubmit(e) {
    if (!sessionId) return;
    var fields = [];
    var els = e.target.elements || [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (!el.name || el.type === 'password' || el.type === 'hidden') continue;
      if (SENSITIVE_FIELD_RE.test(el.name.replace(/[^a-zA-Z0-9]/g, ''))) continue;
      fields.push(el.name);
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
    tptTrack('page_view', Object.assign({ page: newPath, url: w.location.href }, getAttribution()));
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
