const PAGE_META = {
    dashboard: ["Tableau de bord", "Les chiffres viennent de vos recherches enregistrées"],
    projects: ["Projets", "Un dossier pour ranger chaque campagne d'envoi"],
    scraper: ["Scrapers", "Campings, recherche libre et annuaires"],
    schedule: ["Planification", "La recherche part seule à l'heure choisie, tant que cette page reste lancée sur le PC"],
    data: ["Données", "Les fiches déjà trouvées, avec ou sans e-mail"],
    exports: ["Exports", "Les fichiers Excel et CSV déjà téléchargés"],
    proxies: ["Proxies", "Chrome passe par le proxy choisi. Sans proxy, la recherche est directe"],
    settings: ["Paramètres", "Nom affiché et options de lancement"]
};

const WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];

function showPage(name) {
    if (!PAGE_META[name]) name = "dashboard";
    document.querySelectorAll(".page").forEach(page => {
        page.hidden = page.id !== "page-" + name;
    });
    document.querySelectorAll(".nav-btn, .classic-link").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.page === name);
    });
    const meta = PAGE_META[name];
    document.getElementById("pageTitle").textContent = meta[0];
    document.getElementById("pageSubtitle").textContent = meta[1];
    localStorage.setItem("orascrap-page", name);
    if (name === "dashboard") refreshDashboard();
    if (name === "projects") refreshProjects();
    if (name === "schedule") refreshSchedules();
    if (name === "data") refreshContacts();
    if (name === "exports") refreshExports();
    if (name === "proxies") refreshProxies();
    if (name === "settings") refreshSettings();
}

function esc(value) {
    return typeof escapeHtml === "function" ? escapeHtml(value) : String(value ?? "");
}

function currentProjectId() {
    const el = document.getElementById("projectSelect");
    return el && el.value ? el.value : null;
}

async function refreshProjectSelect() {
    const select = document.getElementById("projectSelect");
    const filter = document.getElementById("schedProject");
    if (!select && !filter) return;
    const rows = await fetch("/api/projects").then(r => r.json()).catch(() => []);
    const current = select ? select.value : "";
    const options = '<option value="">Sans projet</option>' + rows.map(row =>
        `<option value="${row.id}">${esc(row.name)}</option>`
    ).join("");
    if (select) {
        select.innerHTML = options;
        if ([...select.options].some(opt => opt.value === current)) select.value = current;
    }
    if (filter) {
        const chosen = filter.value;
        filter.innerHTML = options;
        if ([...filter.options].some(opt => opt.value === chosen)) filter.value = chosen;
    }
    const name = (document.getElementById("settingName") || {}).value || localStorage.getItem("orascrap-name") || "";
    paintUser(name);
}

function paintUser(name) {
    const label = (name || "Ora").trim();
    const chip = document.getElementById("userName");
    const avatar = document.getElementById("userAvatar");
    if (chip) chip.textContent = label || "Ora";
    if (avatar) avatar.textContent = (label.slice(0, 2) || "O").toUpperCase();
}

function formatNumber(value) {
    return Number(value || 0).toLocaleString("fr-FR");
}

async function refreshDashboard() {
    const data = await fetch("/api/overview").then(r => r.json()).catch(() => null);
    const grid = document.getElementById("statGrid");
    if (!data || !grid) return;
    const next = data.next_schedule
        ? data.next_schedule.next_run
        : "aucune";
    const cards = [
        ["Recherches", formatNumber(data.jobs), data.running ? "une recherche est en cours" : "dans l'historique"],
        ["Fiches", formatNumber(data.rows), data.running ? `${formatNumber(data.live_count)} déjà affichées` : "lignes enregistrées"],
        ["Avec e-mail", data.email_rate + " %", formatNumber(data.emails) + " fiches"],
        ["Prochaine fois", next, data.next_schedule ? data.next_schedule.name : "pas de planification"]
    ];
    grid.innerHTML = cards.map(([label, value, hint]) =>
        `<article class="stat-card"><div class="label">${label}</div><div class="value">${esc(value)}</div><div class="hint">${esc(hint)}</div></article>`
    ).join("");

    const max = Math.max(1, ...data.series.map(item => item.rows));
    document.getElementById("chartCard").innerHTML = `
        <h2>Fiches sur 7 jours</h2>
        <div class="bars">${data.series.map(item => `
            <div class="bar-col" title="${item.rows} fiches">
                <div class="bar" style="height:${Math.max(4, Math.round(150 * item.rows / max))}px"></div>
                <span>${esc(item.label)}</span>
            </div>`).join("")}
        </div>`;

    const running = data.running
        ? `<tr><td>En cours</td><td>${esc(data.detail || "Recherche")}</td><td><span class="pill run">En cours</span></td><td>${formatNumber(data.live_count)}</td></tr>`
        : "";
    const recent = (data.recent || []).map(job => `
        <tr>
            <td>${esc((job.date || "").slice(5, 16))}</td>
            <td><button class="linkish" onclick="openHistory(${job.id})">${esc(job.keyword)}</button></td>
            <td><span class="pill done">Terminé</span></td>
            <td>${formatNumber(job.count)}</td>
        </tr>`).join("");
    document.getElementById("recentCard").innerHTML = `
        <h2>Recherches récentes</h2>
        <table class="job-table"><thead><tr><th>Quand</th><th>Recherche</th><th>Statut</th><th>Fiches</th></tr></thead>
        <tbody>${running}${recent || '<tr><td colspan="4" class="empty-note">Aucune recherche pour l\'instant.</td></tr>'}</tbody></table>`;

    const logs = await fetch("/api/activity").then(r => r.json()).catch(() => []);
    document.getElementById("logCard").innerHTML = `
        <h2>Journal</h2>
        ${(logs || []).slice(0, 12).map(line =>
            `<div class="log-line"><span>${esc(line.time)}</span><span class="lvl-${esc(line.level)}">${esc(line.level)}</span><span>${esc(line.message)}</span></div>`
        ).join("") || '<p class="empty-note">Le journal se remplit quand une recherche démarre.</p>'}`;
    document.getElementById("systemState").textContent = data.running ? "Recherche en cours" : "Prêt";
}

function openHistory(id) {
    showPage("scraper");
    if (typeof loadHistoryEntry === "function") loadHistoryEntry(id);
}

async function refreshProjects() {
    const rows = await fetch("/api/projects").then(r => r.json()).catch(() => []);
    const box = document.getElementById("projectList");
    if (!box) return;
    box.innerHTML = rows.length ? rows.map(row => `
        <div class="list-row">
            <div><h3>${esc(row.name)}</h3><p>${esc(row.note || "Sans note")} · ${esc(row.created)}</p></div>
            <button class="btn-quiet" onclick="removeProject(${row.id})">Supprimer</button>
        </div>`).join("") : '<p class="empty-note">Aucun projet. Créez-en un pour ranger les prochaines recherches.</p>';
}

async function createProject(event) {
    event.preventDefault();
    const name = document.getElementById("projectName").value;
    const note = document.getElementById("projectNote").value;
    const res = await fetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, note })
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || "Impossible de créer le projet"); return; }
    document.getElementById("projectForm").reset();
    refreshProjects();
    refreshProjectSelect();
}

async function removeProject(id) {
    await fetch("/api/projects/" + id, { method: "DELETE" });
    refreshProjects();
    refreshProjectSelect();
}

function scraperPayload() {
    const mode = typeof activeMode === "string" ? activeMode : "camping";
    if (mode === "mairie") {
        const kinds = [];
        ["kindMairie:mairie", "kindEpci:epci", "kindCd:cd", "kindCr:cr", "kindOt:ot", "kindScene:scene", "kindFete:fete", "kindAsso:asso", "kindCe:ce", "kindProd:prod"].forEach(pair => {
            const [id, kind] = pair.split(":");
            const box = document.getElementById(id);
            if (box && box.checked) kinds.push(kind);
        });
        return {
            mode,
            payload: {
                region: document.getElementById("mairieRegion").value,
                departement: document.getElementById("mairieDepartement").value,
                scan_sites: document.getElementById("mairieScanSites").checked,
                max_results: parseInt(document.getElementById("mairieMaxResults").value, 10) || 0,
                min_population: parseInt(document.getElementById("mairieMinPopulation").value, 10) || 0,
                kinds
            }
        };
    }
    if (mode === "universal") {
        let query = document.getElementById("universalQuery").value.trim();
        ["universalVille", "universalRegion", "universalPays"].forEach(id => {
            const value = document.getElementById(id).value.trim();
            if (value) query += " " + value;
        });
        return { mode, payload: { query, max_results: parseInt(document.getElementById("universalMaxResults").value, 10) || 20, debug_mode: document.getElementById("universalDebugMode").checked } };
    }
    if (mode === "smart") {
        let query = document.getElementById("smartQuery").value.trim();
        ["smartVille", "smartRegion", "smartPays"].forEach(id => {
            const value = document.getElementById(id).value.trim();
            if (value && !value.includes("Veuillez")) query += " " + value;
        });
        return { mode, payload: { query, max_results: parseInt(document.getElementById("smartMaxResults").value, 10) || 15, debug_mode: document.getElementById("smartDebugMode").checked } };
    }
    return {
        mode: "camping",
        payload: {
            keyword: document.getElementById("keyword").value,
            max_results: parseInt(document.getElementById("maxResults").value, 10) || 50,
            debug_mode: document.getElementById("debugMode").checked,
            sources: typeof selectedSources !== "undefined" && selectedSources.size ? Array.from(selectedSources) : ["capfun"]
        }
    };
}

function planCurrentSearch() {
    const snap = scraperPayload();
    showPage("schedule");
    document.getElementById("schedMode").value = snap.mode;
    document.getElementById("schedPayload").value = JSON.stringify(snap.payload);
    const hint = document.getElementById("schedHint");
    if (hint) hint.textContent = "Réglages copiés depuis Scrapers. Donnez un nom et une heure.";
}

async function createSchedule(event) {
    event.preventDefault();
    let payload = {};
    try {
        payload = JSON.parse(document.getElementById("schedPayload").value || "{}");
    } catch (err) {
        alert("Les réglages copiés ne sont pas lisibles. Revenez sur Scrapers et cliquez Planifier.");
        return;
    }
    const body = {
        name: document.getElementById("schedName").value,
        mode: document.getElementById("schedMode").value,
        project_id: document.getElementById("schedProject").value || null,
        frequency: document.getElementById("schedFrequency").value,
        time: document.getElementById("schedTime").value,
        weekday: document.getElementById("schedWeekday").value,
        once_at: document.getElementById("schedOnce").value.replace("T", " "),
        payload
    };
    const res = await fetch("/api/schedules", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || "Impossible d'enregistrer"); return; }
    document.getElementById("schedName").value = "";
    refreshSchedules();
}

async function refreshSchedules() {
    const rows = await fetch("/api/schedules").then(r => r.json()).catch(() => []);
    const box = document.getElementById("scheduleList");
    if (!box) return;
    box.innerHTML = rows.length ? rows.map(row => `
        <div class="list-row">
            <div>
                <h3>${esc(row.name)}</h3>
                <p>${esc(row.mode)} · ${row.frequency === "weekly" ? WEEKDAYS[row.weekday] || "" : row.frequency} · ${esc(row.time)}
                · prochaine : ${esc(row.next_run || "—")} · dernière : ${esc(row.last_run || "jamais")}</p>
            </div>
            <div class="row-actions">
                <button class="btn-quiet" onclick="toggleSchedule(${row.id}, ${row.enabled ? "false" : "true"})">${row.enabled ? "Pause" : "Activer"}</button>
                <button class="btn-quiet" onclick="runSchedule(${row.id})">Lancer</button>
                <button class="btn-quiet" onclick="removeSchedule(${row.id})">Supprimer</button>
            </div>
        </div>`).join("") : '<p class="empty-note">Aucune planification. Sur Scrapers, réglez une recherche puis cliquez Planifier.</p>';
}

async function toggleSchedule(id, enabled) {
    await fetch("/api/schedules/" + id, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled })
    });
    refreshSchedules();
}

async function runSchedule(id) {
    const res = await fetch("/api/schedules/" + id + "/run", { method: "POST" });
    const data = await res.json();
    if (!res.ok) { alert(data.error || "Lancement impossible"); return; }
    showPage("scraper");
}

async function removeSchedule(id) {
    await fetch("/api/schedules/" + id, { method: "DELETE" });
    refreshSchedules();
}

let contactOffset = 0;

async function refreshContacts() {
    const q = document.getElementById("dataSearch") ? document.getElementById("dataSearch").value : "";
    const only = document.getElementById("dataEmailOnly") && document.getElementById("dataEmailOnly").checked ? "1" : "0";
    const data = await fetch(`/api/contacts?limit=120&offset=${contactOffset}&email=${only}&q=${encodeURIComponent(q)}`).then(r => r.json()).catch(() => ({ total: 0, rows: [] }));
    const box = document.getElementById("dataTable");
    if (!box) return;
    document.getElementById("dataTotal").textContent = `${formatNumber(data.total)} fiches`;
    box.innerHTML = `
        <table class="data-table"><thead><tr><th>Nom</th><th>Type</th><th>E-mail</th><th>Autre e-mail</th><th>Téléphone</th><th>Lieu</th></tr></thead>
        <tbody>${(data.rows || []).map(row => `<tr>
            <td>${esc(row.name)}</td><td>${esc(row.groupe)}</td>
            <td>${esc(row.email || "—")}</td><td>${esc(row.email_extra || "—")}</td>
            <td>${esc(row.phone || "—")}</td><td>${esc(row.city || "—")}</td>
        </tr>`).join("") || '<tr><td colspan="6" class="empty-note">Aucune fiche.</td></tr>'}</tbody></table>`;
}

async function refreshExports() {
    const [exportsLog, history] = await Promise.all([
        fetch("/api/exports").then(r => r.json()).catch(() => []),
        fetch("/history").then(r => r.json()).catch(() => [])
    ]);
    const box = document.getElementById("exportList");
    if (!box) return;
    const done = (exportsLog || []).map(row => `
        <div class="list-row"><div><h3>${esc(row.filename)}</h3><p>${esc(row.date)} · ${formatNumber(row.rows)} lignes · ${esc(row.format)}</p></div></div>`
    ).join("");
    const again = (history || []).slice(0, 12).map(row => `
        <div class="list-row">
            <div><h3>${esc(row.keyword)}</h3><p>${esc(row.date)} · ${formatNumber(row.count)} fiches</p></div>
            <div class="row-actions">
                <button class="btn-quiet" onclick="exportFromHistory(${row.id}, 'xlsx', 'all')">Excel</button>
                <button class="btn-quiet" onclick="exportFromHistory(${row.id}, 'csv', 'emails')">CSV e-mails</button>
            </div>
        </div>`).join("");
    box.innerHTML = `<h2>Fichiers déjà générés</h2>${done || '<p class="empty-note">Aucun téléchargement pour l\'instant.</p>'}
        <h2 style="margin-top:18px">Retélécharger une recherche</h2>${again || '<p class="empty-note">L\'historique est vide.</p>'}`;
}

async function refreshProxies() {
    const [rows, settings] = await Promise.all([
        fetch("/api/proxies").then(r => r.json()).catch(() => []),
        fetch("/api/settings").then(r => r.json()).catch(() => ({}))
    ]);
    const mode = document.getElementById("proxyMode");
    if (mode && settings.proxy_mode) mode.value = settings.proxy_mode;
    const box = document.getElementById("proxyList");
    if (!box) return;
    box.innerHTML = rows.length ? rows.map(row => `
        <div class="list-row">
            <div>
                <h3>${esc(row.label)}</h3>
                <p>${esc(row.scheme)}://${esc(row.host)}:${row.port}${row.username ? " · identifiant" : ""}${row.enabled ? "" : " · désactivé"}</p>
            </div>
            <div class="row-actions">
                <button class="btn-quiet" onclick="useProxy(${row.id})">Utiliser</button>
                <button class="btn-quiet" onclick="testProxy(${row.id})">Tester</button>
                <button class="btn-quiet" onclick="removeProxy(${row.id})">Supprimer</button>
            </div>
        </div>`).join("") : '<p class="empty-note">Aucun proxy. La recherche part directement de ce PC.</p>';
    const active = document.getElementById("proxyActive");
    if (active) {
        const chosen = rows.find(row => row.id === settings.active_proxy_id);
        active.textContent = settings.proxy_mode === "off"
            ? "Mode direct, sans proxy."
            : (chosen ? `Proxy utilisé : ${chosen.label}` : "Ajoutez un proxy ou repassez en direct.");
    }
}

async function createProxy(event) {
    event.preventDefault();
    const body = {
        label: document.getElementById("proxyLabel").value,
        scheme: document.getElementById("proxyScheme").value,
        host: document.getElementById("proxyHost").value,
        port: document.getElementById("proxyPort").value,
        username: document.getElementById("proxyUser").value,
        password: document.getElementById("proxyPass").value
    };
    const res = await fetch("/api/proxies", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || "Proxy refusé"); return; }
    document.getElementById("proxyForm").reset();
    refreshProxies();
}

async function saveProxyMode() {
    await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proxy_mode: document.getElementById("proxyMode").value })
    });
    refreshProxies();
}

async function useProxy(id) {
    await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proxy_mode: "selected", active_proxy_id: id })
    });
    refreshProxies();
}

async function testProxy(id) {
    const res = await fetch("/api/proxies/" + id + "/test", { method: "POST" });
    const data = await res.json();
    alert(data.ok ? "Le proxy répond." : (data.error || "Le proxy ne répond pas."));
}

async function removeProxy(id) {
    await fetch("/api/proxies/" + id, { method: "DELETE" });
    refreshProxies();
}

async function refreshSettings() {
    const settings = await fetch("/api/settings").then(r => r.json()).catch(() => ({}));
    document.getElementById("settingName").value = settings.display_name || "";
    document.getElementById("settingChrome").checked = !!settings.chrome_visible;
    const auto = document.getElementById("autoUpdate");
    if (auto) auto.checked = settings.auto_update !== false;
    paintUser(settings.display_name);
    checkUpdate();
}

async function saveSettings(event) {
    event.preventDefault();
    const body = {
        display_name: document.getElementById("settingName").value,
        chrome_visible: document.getElementById("settingChrome").checked,
        auto_update: document.getElementById("autoUpdate").checked
    };
    const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
    });
    const data = await res.json();
    localStorage.setItem("orascrap-name", data.display_name || "");
    paintUser(data.display_name);
    ["debugMode", "universalDebugMode", "smartDebugMode"].forEach(id => {
        const box = document.getElementById(id);
        if (box) box.checked = !!data.chrome_visible;
    });
    alert("Paramètres enregistrés.");
}

document.getElementById("globalSearch")?.addEventListener("input", (event) => {
    const page = localStorage.getItem("orascrap-page") || "dashboard";
    if (page === "data") {
        const field = document.getElementById("dataSearch");
        if (field) field.value = event.target.value;
        contactOffset = 0;
        refreshContacts();
    }
    if (page === "scraper") {
        const field = document.getElementById("sourceSearch");
        if (field) {
            field.value = event.target.value;
            if (typeof renderSources === "function") renderSources();
        }
    }
});

function paintUpdate(state) {
    const banner = document.getElementById("updateBanner");
    const message = document.getElementById("updateMessage");
    const notes = document.getElementById("updateNotes");
    if (!state) return;
    if (message && (state.error || state.message || state.current)) {
        if (state.error) {
            message.textContent = state.error;
        } else if (state.current) {
            const latest = state.latest || "inconnue";
            const verdict = state.update_available
                ? `La version ${latest} est disponible.`
                : (state.message || "Vous êtes à jour.");
            message.textContent = `Installée : ${state.current}. Dernière : ${latest}. ${verdict}`;
        } else {
            message.textContent = state.message;
        }
    }
    if (notes) notes.textContent = state.notes || "";
    if (!banner) return;
    if (state.restarting) {
        banner.hidden = false;
        banner.textContent = "Mise à jour installée. L'application redémarre…";
        return;
    }
    if (state.update_available) {
        banner.hidden = false;
        banner.textContent = `Version ${state.latest} disponible. Elle s'installe au lancement, ou depuis Paramètres.`;
        return;
    }
    banner.hidden = true;
}

async function checkUpdate() {
    const message = document.getElementById("updateMessage");
    const button = document.getElementById("checkUpdateBtn");
    if (message) message.textContent = "Vérification sur GitHub…";
    if (button) button.disabled = true;
    try {
        const res = await fetch("/api/update/check", { method: "POST" });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            paintUpdate({ error: data.error || "La vérification a échoué." });
            return;
        }
        paintUpdate(data);
    } catch (err) {
        paintUpdate({ error: "La vérification n'a pas abouti. Relancez Lancer.bat puis réessayez." });
    } finally {
        if (button) button.disabled = false;
    }
}

async function runUpdate() {
    const res = await fetch("/api/update", { method: "POST" });
    const data = await res.json();
    if (!res.ok) {
        alert(data.error || "Mise à jour impossible");
        return;
    }
    paintUpdate(data);
}

function watchUpdate() {
    const tick = () => {
        fetch("/api/update/status").then(r => r.json()).then(state => {
            paintUpdate(state);
            if (state.busy || state.restarting) setTimeout(tick, 1500);
        }).catch(() => {});
    };
    tick();
}

showPage(localStorage.getItem("orascrap-page") || (document.documentElement.getAttribute("data-theme") === "basic" ? "scraper" : "dashboard"));
refreshProjectSelect();
watchUpdate();
setInterval(() => {
    if ((localStorage.getItem("orascrap-page") || "dashboard") === "dashboard") refreshDashboard();
}, 8000);
