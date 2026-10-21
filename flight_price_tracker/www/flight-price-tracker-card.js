import {TrackerModel, chartMarkup, escapeHTML, pointDetail} from './flight-chart.mjs?v=0.6.1';

class FlightPriceTrackerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._model = new TrackerModel();
    this._hidden = new Map();
    this._generation = 0;
    this._busy = false;
    this._visibility = () => {
      if (document.hidden) this._stop();
      else this._refresh();
    };
    this.shadowRoot.addEventListener('click', (event) => this._interact(event));
    this.shadowRoot.addEventListener('focusin', (event) => this._point(event.target));
    this.shadowRoot.addEventListener('pointerover', (event) => this._point(event.target));
    this.shadowRoot.addEventListener('keydown', (event) => {
      if (['Enter', ' '].includes(event.key) && event.target.hasAttribute('data-point')) {
        event.preventDefault();
        this._point(event.target);
      }
    });
  }

  setConfig(config) {
    if (config.show_disabled !== undefined && typeof config.show_disabled !== 'boolean') throw new Error('show_disabled must be boolean');
    const interval = config.refresh_interval ?? 60;
    if (!Number.isInteger(interval) || interval < 30 || interval > 3600) throw new Error('refresh_interval must be 30–3600 seconds');
    if (config.title !== undefined && typeof config.title !== 'string') throw new Error('title must be text');
    this._config = {...config, refresh_interval: interval};
    this._showDisabled = config.show_disabled ?? true;
    this._stop();
    this._model = new TrackerModel();
    this._render();
    this._refresh();
  }

  set hass(hass) {
    const previousUser = this._hass?.user?.id;
    this._hass = hass;
    if (previousUser && previousUser !== hass.user?.id) {
      this._stop();
      this._model = new TrackerModel();
      this._render();
    }
    // HA calls this setter on unrelated state updates; never reload per state event.
    if (!this._busy && !this._timer) this._refresh();
  }

  connectedCallback() {
    document.addEventListener('visibilitychange', this._visibility);
    this._refresh();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visibility);
    this._stop();
  }
  _stop() {
    this._generation++;
    clearTimeout(this._timer);
    this._timer = null;
    this._busy = false;
  }
  async _refresh() {
    if (!this.isConnected || !this._hass || !this._config || this._busy || document.hidden) return;
    clearTimeout(this._timer);
    this._timer = null;
    this._busy = true;
    const generation = this._generation;
    const active = () => generation === this._generation && this.isConnected && !document.hidden;
    this._render();
    try {
      await this._model.refresh(this._hass, this._showDisabled, active, () => this._render());
    } finally {
      if (active()) {
        this._busy = false;
        this._render();
        this._timer = setTimeout(() => {this._timer = null; this._refresh();}, this._config.refresh_interval * 1000);
      }
    }
  }

  _interact(event) {
    const target = event.target.closest?.('button, [data-point]');
    if (!target) {
      // A tablet user can tap near a point instead of hitting a tiny SVG circle.
      const svg = event.target.closest?.('svg');
      if (svg) {
        const rect = svg.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width * svg.viewBox.baseVal.width;
        const y = (event.clientY - rect.top) / rect.height * svg.viewBox.baseVal.height;
        if (y < 10 || y > 245 || x < 30 || x > svg.viewBox.baseVal.width - 5) return;
        let nearest, distance = Infinity;
        for (const point of svg.querySelectorAll('[data-point]')) {
          const next = Math.hypot(point.cx.baseVal.value - x, point.cy.baseVal.value - y);
          if (next < distance) { nearest = point; distance = next; }
        }
        if (nearest) this._point(nearest);
      }
      return;
    }
    if (target.dataset.action === 'refresh') this._refresh();
    else if (target.dataset.action === 'disabled') {
      this._showDisabled = !this._showDisabled;
      this._stop();
      this._refresh();
    } else if (target.dataset.series) {
      const id = target.closest('section').dataset.check;
      const hidden = this._hidden.get(id) || new Set();
      if (hidden.has(target.dataset.series)) hidden.delete(target.dataset.series);
      else hidden.add(target.dataset.series);
      this._hidden.set(id, hidden);
      this._render();
    } else this._point(target);
  }

  _point(target) {
    if (!target.hasAttribute?.('data-point')) return;
    const section = target.closest('section');
    const row = this._model.history.get(section?.dataset.check)?.rows[Number(target.dataset.point)];
    if (row) section.querySelector('.detail').textContent = pointDetail(row);
  }

  _render() {
    if (!this._config) return;
    const focused = this.shadowRoot.activeElement;
    const sectionId = focused?.closest('section')?.dataset.check;
    const focusKey = ['action', 'series', 'point'].find((key) => focused?.dataset[key] !== undefined);
    const focusValue = focusKey && focused.dataset[focusKey];
    const esc = escapeHTML, model = this._model;
    const panels = model.checks.filter((c) => this._showDisabled || c.enabled).map((check) => {
      const entry = model.history.get(check.id);
      return `<section data-check="${esc(check.id)}">
        <h3>${esc(check.origin)} → ${esc(check.destination)} ${check.enabled ? '' : '· Wyłączony'}</h3>
        <p class="muted">${esc(check.departureDateFrom)} – ${esc(check.departureDateTo)} · ${esc(check.id)}</p>
        ${model.errors.has(check.id) ? '<p role="alert">Nie udało się odczytać historii. Poprzednie dane mogą być nieaktualne. Użyj Odśwież, aby ponowić.</p>' : ''}
        ${entry ? chartMarkup(check, entry.rows, this._hidden.get(check.id)) : '<p class="muted">Oczekiwanie na dane…</p>'}
        <p class="detail" aria-live="polite">Dotknij punktu, najedź kursorem lub wybierz go klawiaturą, aby zobaczyć szczegóły lotu.</p>
      </section>`;
    }).join('');
    this.shadowRoot.innerHTML = `<style>
      :host { display:block; color:var(--primary-text-color, #202124); font-family:var(--ha-font-family-body, system-ui, sans-serif); }
      ha-card { display:block; padding:16px; }
      header { display:flex; flex-wrap:wrap; align-items:center; gap:10px; }
      h2 { flex:1; margin:0; font-size:1.3em; } h3 { margin:0; font-size:1.15em; }
      button { min-height:44px; border:1px solid var(--divider-color, #d4d8de); border-radius:8px;
        padding:8px 12px; background:var(--secondary-background-color, #f3f4f6); color:var(--primary-text-color, #202124); font:inherit; cursor:pointer; }
      button:disabled { opacity:.6; cursor:wait; }
      button:focus-visible, circle:focus-visible { outline:3px solid var(--primary-color); outline-offset:2px; }
      section { margin-top:20px; border-top:1px solid var(--divider-color); padding-top:20px; min-width:0; }
      p { overflow-wrap:anywhere; } .muted, .detail { color:var(--secondary-text-color); font-size:.85em; }
      .detail { min-height:3em; line-height:1.5; } [role=alert] { color:var(--error-color, #b3261e); }
      .plot { overflow-x:auto; } svg { display:block; width:100%; min-width:480px; }
      svg .axis-day { font-size:16px; }
      svg text { fill:var(--secondary-text-color, #60656f); font-size:18px; } .grid { stroke:var(--divider-color, #aaa); stroke-width:1; }
      circle { cursor:pointer; } .legend { display:flex; flex-wrap:wrap; gap:6px; max-height:150px; overflow:auto; }
      .series { font-size:.8em; border-width:2px; } .series[aria-pressed=false] { opacity:.5; text-decoration:line-through; }
    </style><ha-card>
      <header><h2>${esc(this._config.title || 'Ceny lotów')}</h2>
      <button data-action="refresh" ${this._busy ? 'disabled' : ''}>${this._busy ? 'Ładowanie…' : 'Odśwież'}</button>
      <button data-action="disabled" aria-pressed="${this._showDisabled}">Wyłączone: ${this._showDisabled ? 'pokaż' : 'ukryj'}</button></header>
      ${model.listError ? '<p role="alert">Nie udało się pobrać listy checków. Widoczne dane mogą być nieaktualne.</p>' : ''}
      ${panels || `<p class="muted">${this._busy ? 'Pobieranie checków…' : model.listError ? 'Lista niedostępna.' : 'Brak checków do wyświetlenia.'}</p>`}
    </ha-card>`;
    if (focusKey) {
      const scope = sectionId ? this.shadowRoot.querySelector(`section[data-check="${CSS.escape(sectionId)}"]`) : this.shadowRoot;
      scope?.querySelector(`[data-${focusKey}="${CSS.escape(focusValue)}"]`)?.focus({preventScroll: true});
    }
  }
  getCardSize() { return Math.max(3, this._model.checks.length * 6); }
  static getStubConfig() { return {show_disabled: true, refresh_interval: 60}; }
}
if (!customElements.get('flight-price-tracker-card')) customElements.define('flight-price-tracker-card', FlightPriceTrackerCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === 'flight-price-tracker-card')) window.customCards.push({
  type: 'flight-price-tracker-card', name: 'Flight Price Tracker',
  description: 'Dynamiczne wykresy cen — jeden wykres na check',
});
