"""
DDLnzb Bridge - HTML-Rendering der Web-Oberflaeche

Bewusst ohne Template-Engine: die Bridge hat keine Jinja2-Abhaengigkeit, und
eine statische Seite mit ein paar Formularen braucht keine. Das haelt das
Container-Image schmal und die Abhaengigkeiten unveraendert.

Das Markup folgt dem Muster des Admin-Portals der DDLnzb-Website: Karte mit
Rand und `shadow-sm`, Beschriftung ueber dem Eingabefeld, Badges fuer den
Zustand. Die Farbwerte kommen aus `static/bridge.css`, das die Tokens aus
`frontend/src/app/globals.css` spiegelt.
"""

from html import escape

# --------------------------------------------------------------------- Icons

_ICONS = {
    "brand": (
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M12 2 3 7v10l9 5 9-5V7z"/><path d="m3 7 9 5 9-5"/><path d="M12 12v10"/>'
        "</svg>"
    ),
    "api": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/>'
        '<path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/></svg>'
    ),
    "download": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/>'
        '<path d="M12 15V3"/></svg>'
    ),
    "sliders": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M4 21v-7"/><path d="M4 10V3"/><path d="M12 21v-9"/><path d="M12 8V3"/>'
        '<path d="M20 21v-5"/><path d="M20 12V3"/><path d="M1 14h6"/><path d="M9 8h6"/>'
        '<path d="M17 16h6"/></svg>'
    ),
    "server": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" '
        'y="14" rx="2"/><path d="M6 6h.01"/><path d="M6 18h.01"/></svg>'
    ),
    "gauge": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/></svg>'
    ),
    "refresh": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M21 12a9 9 0 0 0-15-6.7L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 '
        '0 0 0 15 6.7L21 16"/><path d="M16 16h5v5"/></svg>'
    ),
    "logout": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/>'
        '<path d="M21 12H9"/></svg>'
    ),
    "eye": (
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" '
        'cy="12" r="3"/></svg>'
    ),
    "check": (
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M20 6 9 17l-5-5"/></svg>'
    ),
    "cross": (
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        '<path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>'
    ),
}


def icon(name: str) -> str:
    """Inline-SVG fuer einen Icon-Namen."""
    return _ICONS.get(name, "")


def badge(label: str, kind: str = "", dot: bool = False) -> str:
    """Zustands-Badge. `kind`: ok | warn | err | info | "" (neutral)."""
    classes = "badge" if not kind else f"badge badge-{kind}"
    inner = f'<span class="dot"></span>{escape(label)}' if dot else escape(label)
    return f'<span class="{classes}">{inner}</span>'


def _flash(kind: str, text: str) -> str:
    return f'<div class="flash flash-{kind}">{escape(text)}</div>'


_FLASH_TEXTS = {
    "saved": ("ok", "Konfiguration gespeichert. Die neuen Werte gelten sofort."),
    "reconnect": ("ok", "JDownloader neu verbunden."),
    "reset": ("ok", "Zurueckgesetzt. Es gelten wieder die Werte aus der Umgebung."),
    "error": ("err", "Die Aenderung konnte nicht gespeichert werden."),
    "test-failed": ("err", "Die Verbindungstests sind fehlgeschlagen."),
}


def flash_from_code(code: str | None) -> str:
    """Flash-Meldung aus dem Redirect-Code bauen."""
    if not code or code not in _FLASH_TEXTS:
        return ""
    kind, text = _FLASH_TEXTS[code]
    return _flash(kind, text)


def _nav_link(target: str, label: str, icon_name: str) -> str:
    return (
        f'<a class="nav-link" href="#{target}">{icon(icon_name)}'
        f"<span>{escape(label)}</span></a>"
    )


def layout(title: str, content: str, *, with_nav: bool = True) -> str:
    """Grundgeruest einer Seite."""
    nav = ""
    if with_nav:
        nav = f"""
    <aside class="sidebar">
      <div class="brand">
        <span class="brand-mark">{icon("brand")}</span>
        <span class="brand-text">
          <span class="brand-name">DDLnzb Bridge</span>
          <span class="brand-sub">Download-Client</span>
        </span>
      </div>
      <nav class="nav">
        <span class="nav-title">Einrichtung</span>
        {_nav_link("uebersicht", "Uebersicht", "gauge")}
        {_nav_link("api", "DDLnzb API", "api")}
        {_nav_link("jdownloader", "JDownloader", "download")}
        {_nav_link("downloads", "Downloads", "sliders")}
        {_nav_link("system", "System", "server")}
      </nav>
      <div class="sidebar-foot">
        <span>Port 6789 &middot; SABnzbd-API</span>
        <form method="post" action="/ui/logout">
          <button class="btn btn-ghost btn-sm" type="submit">{icon("logout")}<span>Abmelden</span></button>
        </form>
      </div>
    </aside>"""

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<link rel="stylesheet" href="/ui/static/bridge.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2300d9c0' stroke-width='2'%3E%3Cpath d='M12 2 3 7v10l9 5 9-5V7z'/%3E%3C/svg%3E">
</head>
<body>
<div class="shell">{nav}
<main class="content">
{content}
</main>
</div>
<script src="/ui/static/bridge.js" defer></script>
</body>
</html>"""


def auth_page(title: str, heading: str, intro: str, form: str, *, wide: bool = False) -> str:
    """Zentrierte Seite fuer Anmeldung und Ersteinrichtung."""
    card_class = "auth-card is-wide" if wide else "auth-card"
    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>{escape(title)}</title>
<link rel="stylesheet" href="/ui/static/bridge.css">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2300d9c0' stroke-width='2'%3E%3Cpath d='M12 2 3 7v10l9 5 9-5V7z'/%3E%3C/svg%3E">
</head>
<body>
<div class="auth">
  <div class="{card_class}">
    <div class="auth-brand">
      <span class="brand-mark">{icon("brand")}</span>
      <span class="brand-text">
        <span class="brand-name">DDLnzb Bridge</span>
        <span class="brand-sub">Download-Client</span>
      </span>
    </div>
    <h1 class="auth-title">{escape(heading)}</h1>
    <p class="auth-sub">{escape(intro)}</p>
    {form}
  </div>
</div>
</body>
</html>"""


def login_page(error: str | None = None) -> str:
    """Anmeldeseite - der Bridge-Key wird als Passwort eingegeben."""
    flash = _flash("err", error) if error else ""
    form = f"""
    {flash}
    <form method="post" action="/ui/login" class="card-body" style="padding:0">
      <div class="field">
        <label class="label" for="key">Bridge-API-Key</label>
        <input class="input" id="key" name="key" type="password" required autofocus
               autocomplete="current-password" placeholder="DDLNZB_API_KEY">
        <span class="hint">Derselbe Key, den auch Radarr, Sonarr und die *arr-Apps verwenden.</span>
      </div>
      <button class="btn btn-primary" type="submit">Anmelden</button>
    </form>"""
    return auth_page(
        "DDLnzb Bridge - Anmeldung",
        "Anmeldung",
        "Die Oberflaeche ist mit dem Bridge-Key geschuetzt.",
        form,
    )


def setup_page(error: str | None = None) -> str:
    """Ersteinrichtung - nur erreichbar, solange kein Key konfiguriert ist."""
    flash = _flash("err", error) if error else ""
    form = f"""
    {flash}
    <div class="steps">
      <div class="step">Setup-Token aus dem Container-Log holen:
        <code>docker logs ddlnzb-bridge</code></div>
      <div class="step">DDLnzb-API-Key aus dem Portal unter <code>API-Keys</code> eintragen.</div>
      <div class="step">JDownloader-Zugang und Zielordner ergaenzen - beides ist spaeter
        in der Oberflaeche aenderbar.</div>
    </div>
    <form method="post" action="/ui/setup" class="card-body" style="padding:0;gap:1.1rem">
      <div class="field">
        <label class="label" for="setup_token">Setup-Token</label>
        <input class="input" id="setup_token" name="setup_token" type="password" required
               autocomplete="off" placeholder="Token aus dem Container-Log">
      </div>
      <div class="field">
        <label class="label" for="api_key">DDLnzb-API-Key</label>
        <input class="input" id="api_key" name="api_key" type="password" required
               autocomplete="off" placeholder="Pflicht - ohne Key ist die Bridge nutzlos">
        <span class="hint">Wird dauerhaft gespeichert und ist danach der Schluessel fuer die
          Oberflaeche und fuer die *arr-Apps.</span>
      </div>
      <div class="field">
        <label class="label" for="download_dir">Download-Zielordner</label>
        <input class="input" id="download_dir" name="download_dir" type="text" placeholder="/downloads">
      </div>
      <div class="grid-2">
        <div class="field">
          <label class="label" for="jd_email">JDownloader E-Mail</label>
          <input class="input" id="jd_email" name="jd_email" type="email" autocomplete="off">
        </div>
        <div class="field">
          <label class="label" for="jd_password">JDownloader Passwort</label>
          <input class="input" id="jd_password" name="jd_password" type="password" autocomplete="off">
        </div>
      </div>
      <div class="field">
        <label class="label" for="jd_device">JDownloader Geraete-Name</label>
        <input class="input" id="jd_device" name="jd_device" type="text"
               placeholder="Name des Geraets in my.jdownloader.org">
      </div>
      <button class="btn btn-primary" type="submit">Einrichtung abschliessen</button>
    </form>"""
    return auth_page(
        "DDLnzb Bridge - Ersteinrichtung",
        "Ersteinrichtung",
        "Der Bridge ist noch nicht konfiguriert. Der Setup-Token steht im Container-Log.",
        form,
        wide=True,
    )


def _kpi(label: str, value: str, meta: str = "", extra: str = "") -> str:
    meta_html = f'<div class="kpi-meta">{escape(meta)}</div>' if meta else ""
    return (
        f'<div class="kpi"><div class="kpi-label">{escape(label)}</div>'
        f'<div class="kpi-value">{value}</div>{meta_html}{extra}</div>'
    )


def dashboard_page(view: dict) -> str:
    """Die Hauptseite mit Status und Konfigurationsformular."""
    flash = flash_from_code(view.get("flash"))

    api_badge = (
        badge("erreichbar", "ok", dot=True)
        if view["api_reachable"]
        else badge("nicht erreichbar", "err", dot=True)
    )
    jd_badge = (
        badge("verbunden", "ok", dot=True)
        if view["jd_connected"]
        else badge("nicht verbunden", "warn", dot=True)
    )

    kpis = "".join(
        [
            _kpi(
                "DDLnzb API",
                api_badge,
                view["api_url"],
            ),
            _kpi(
                "JDownloader",
                jd_badge,
                view["jd_device_name"] or view["jd_device"] or "kein Geraet gewaehlt",
            ),
            _kpi(
                "Queue",
                str(view["queue_total"]),
                f"{view['queue_queued']} wartend · {view['queue_downloading']} laufend",
            ),
            _kpi(
                "Konfiguration",
                view["config_source"],
                f"Settings: {view['settings_path']}",
            ),
        ]
    )

    api_key_badge = (
        badge("gesetzt", "ok") if view["api_key_set"] else badge("nicht gesetzt", "err")
    )
    jd_password_badge = (
        badge("gesetzt", "ok") if view["jd_password_set"] else badge("nicht gesetzt", "warn")
    )

    content = f"""
<header class="page-head">
  <div>
    <h1 class="page-title">Bridge einrichten</h1>
    <p class="page-sub">Konfiguration, Verbindungstests und Betriebszustand des Download-Clients.</p>
  </div>
  <div class="head-badges">
    {api_badge}
    {jd_badge}
    {badge(f"Version {view['version']}")}
  </div>
</header>

{flash}

<div class="kpis">{kpis}</div>

<form method="post" action="/ui/settings" id="settings-form">
  <section class="card" id="api">
    <div class="card-head">
      <div>
        <h2 class="card-title">{icon("api")}DDLnzb API</h2>
        <p class="card-desc">Der Bridge fragt hier die echten Links zu einem Release ab.
          Jeder Aufruf zaehlt gegen das taegliche Download-Limit des Kontos.</p>
      </div>
      <button class="btn btn-outline btn-sm" type="button" data-test="api">
        {icon("refresh")}<span>Verbindung testen</span>
      </button>
    </div>
    <div class="card-body">
      <div class="field">
        <label class="label" for="api_url">API-URL</label>
        <input class="input" id="api_url" type="text" value="{escape(view['api_url'])}" disabled>
        <span class="hint">Die API-URL bringt der Bridge mit - sie kommt aus der
          Deployment-Konfiguration (<code>DDLNZB_API_URL</code>) und ist hier bewusst nicht
          editierbar.</span>
      </div>
      <div class="field">
        <label class="label" for="api_key">API-Key {api_key_badge}</label>
        <div class="secret-wrap">
          <input class="input" id="api_key" name="api_key" type="password" autocomplete="off"
                 placeholder="{'(unveraendert)' if view['api_key_set'] else 'DDLNZB_API_KEY'}">
          <button class="btn btn-outline btn-sm" type="button" data-toggle-secret="api_key">
            {icon("eye")}<span>Anzeigen</span>
          </button>
        </div>
        <span class="hint">Leer lassen, um den gespeicherten Key beizubehalten.</span>
      </div>
      <div class="result" data-result="api">
        <div class="result-row"><span>DDLnzb API</span><span data-result-value="api"></span></div>
        <div class="result-detail" data-result-detail="api"></div>
      </div>
    </div>
  </section>

  <section class="card" id="jdownloader">
    <div class="card-head">
      <div>
        <h2 class="card-title">{icon("download")}JDownloader</h2>
        <p class="card-desc">Ueber My JDownloader werden die Links an das Zielgeraet
          uebergeben. Ohne Zugangsdaten bleibt die Queue liegen.</p>
      </div>
      <button class="btn btn-outline btn-sm" type="button" data-test="jdownloader">
        {icon("refresh")}<span>Neu verbinden</span>
      </button>
    </div>
    <div class="card-body">
      <div class="grid-2">
        <div class="field">
          <label class="label" for="jd_email">E-Mail</label>
          <input class="input" id="jd_email" name="jd_email" type="email" autocomplete="off"
                 value="{escape(view['jd_email'])}">
        </div>
        <div class="field">
          <label class="label" for="jd_password">Passwort {jd_password_badge}</label>
          <div class="secret-wrap">
            <input class="input" id="jd_password" name="jd_password" type="password"
                   autocomplete="off"
                   placeholder="{'(unveraendert)' if view['jd_password_set'] else 'Passwort'}">
            <button class="btn btn-outline btn-sm" type="button" data-toggle-secret="jd_password">
              {icon("eye")}<span>Anzeigen</span>
            </button>
          </div>
        </div>
      </div>
      <div class="grid-2">
        <div class="field">
          <label class="label" for="jd_device">Geraete-Name</label>
          <input class="input" id="jd_device" name="jd_device" type="text"
                 value="{escape(view['jd_device'])}" placeholder="Name in my.jdownloader.org">
          <span class="hint">Der Name muss exakt dem Geraetenamen im Account entsprechen.</span>
        </div>
        <div class="field">
          <label class="label" for="jd_app_key">App-Key</label>
          <input class="input" id="jd_app_key" name="jd_app_key" type="text"
                 value="{escape(view['jd_app_key'])}" placeholder="DDLnzb">
        </div>
      </div>
      <div class="result" data-result="jdownloader">
        <div class="result-row"><span>JDownloader</span><span data-result-value="jdownloader"></span></div>
        <div class="result-detail" data-result-detail="jdownloader"></div>
      </div>
    </div>
  </section>

  <section class="card" id="downloads">
    <div class="card-head">
      <div>
        <h2 class="card-title">{icon("sliders")}Downloads &amp; Pfade</h2>
        <p class="card-desc">Zielordner und das Verhalten beim Entpacken der Archive.</p>
      </div>
    </div>
    <div class="card-body">
      <div class="field">
        <label class="label" for="download_dir">Download-Zielordner</label>
        <input class="input" id="download_dir" name="download_dir" type="text"
               value="{escape(view['download_dir'])}" placeholder="/downloads">
        <span class="hint">Der Ordner muss fuer den Bridge beschreibbar und fuer JDownloader
          identisch erreichbar sein.</span>
      </div>
      <div class="grid-2">
        <label class="switch" for="extract_archives">
          <input type="checkbox" id="extract_archives" name="extract_archives"
                 {'checked' if view['extract_archives'] else ''}>
          <span class="switch-text">Archive entpacken
            <small>Entpackte Inhalte an JDownloader uebergeben</small></span>
        </label>
        <label class="switch" for="delete_archives">
          <input type="checkbox" id="delete_archives" name="delete_archives"
                 {'checked' if view['delete_archives'] else ''}>
          <span class="switch-text">Archive danach loeschen
            <small>Platz freigeben, sobald entpackt wurde</small></span>
        </label>
      </div>
    </div>
  </section>

  <section class="card" id="system">
    <div class="card-head">
      <div>
        <h2 class="card-title">{icon("server")}System</h2>
        <p class="card-desc">Laufzeit, Speicherort der Konfiguration und Log-Stufe.</p>
      </div>
      <button class="btn btn-danger btn-sm" type="button" data-reset>Zuruecksetzen</button>
    </div>
    <div class="card-body">
      <div class="field" style="max-width:320px">
        <label class="label" for="log_level">Log-Stufe</label>
        <select class="select" id="log_level" name="log_level">
          {"".join(f'<option value="{level}"{" selected" if view["log_level"] == level else ""}>{level}</option>' for level in ("DEBUG", "INFO", "WARNING", "ERROR"))}
        </select>
        <span class="hint">Wirkt nach einem Neustart des Containers.</span>
      </div>
      <dl class="kv">
        <dt>Bridge-Port</dt>
        <dd>6789 (im Docker-Image fest vorgegeben)</dd>
        <dt>Betrieb seit</dt>
        <dd>{escape(view['started_at'])}</dd>
        <dt>Settings-Datei</dt>
        <dd><code>{escape(view['settings_path'])}</code></dd>
        <dt>Konfigurationsquelle</dt>
        <dd>{escape(view['config_source'])}</dd>
        <dt>Kategorie-Mapping</dt>
        <dd>{escape(view['category_summary'])}</dd>
      </dl>
    </div>
  </section>
</form>

<div class="savebar">
  <span class="savebar-hint">Aenderungen gelten sofort - kein Neustart noetig.</span>
  <div class="savebar-actions">
    <button class="btn btn-ghost" type="button" onclick="location.reload()">Verwerfen</button>
    <button class="btn btn-primary" type="submit" form="settings-form">Speichern</button>
  </div>
</div>"""

    return layout("DDLnzb Bridge - Einrichtung", content)
