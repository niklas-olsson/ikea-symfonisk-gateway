class SymfoniskGatewayPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
  }

  set hass(hass) {
    const oldHass = this._hass;
    this._hass = hass;
    this._updateState(oldHass);
  }

  set config(config) {
    this._config = config;
    this._renderBase();
  }

  _updateState(oldHass) {
    if (!this._hass || !this._config) return;

    // Find our entities robustly by checking for our custom attributes
    const mediaPlayerId = Object.keys(this._hass.states).find(id => {
        if (!id.startsWith('media_player.')) return false;
        const stateObj = this._hass.states[id];
        return stateObj.attributes && (
            stateObj.attributes.friendly_name === 'SYMFONISK Bridge' ||
            stateObj.attributes.session_id !== undefined ||
            stateObj.attributes.bridge_state !== undefined
        );
    });

    const state = {
        mediaPlayer: this._hass.states[mediaPlayerId],
    };

    if (JSON.stringify(state) !== JSON.stringify(this._state)) {
        this._state = state;
        this._updateUI();
    }
  }

  _renderBase() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          padding: 16px;
          background-color: var(--primary-background-color);
          color: var(--primary-text-color);
          font-family: var(--paper-font-body1_-_font-family);
        }
        .container {
          max-width: 800px;
          margin: 0 auto;
          display: flex;
          flex-direction: column;
          gap: 24px;
        }
        ha-card {
          padding: 16px;
        }
        .header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 16px;
        }
        .status-tag {
          padding: 4px 12px;
          border-radius: 20px;
          font-weight: 600;
          text-transform: uppercase;
          font-size: 0.7rem;
        }
        .status-idle { background: #eee; color: #666; }
        .status-playing { background: #e6f4ea; color: #1e8e3e; }
        .status-buffering { background: #fef7e0; color: #f9ab00; }
        .status-error { background: #fce8e6; color: #d93025; }
        .actions { display: flex; gap: 8px; margin-top: 16px; }
        .details { font-size: 0.9rem; color: var(--secondary-text-color); margin-top: 8px; }
      </style>
      <div class="container">
        <ha-card>
          <div class="header">
            <h1>SYMFONISK Bridge</h1>
            <span id="session-status" class="status-tag status-idle">Idle</span>
          </div>
          <div id="session-info">
             <p id="session-text">No active playback</p>
             <div id="session-details" class="details"></div>
          </div>
          <div class="actions">
            <mwc-button id="play-btn" raised>Play</mwc-button>
            <mwc-button id="stop-btn" outlined>Stop</mwc-button>
            <mwc-button id="recover-btn" outlined style="display:none">Recover</mwc-button>
          </div>
        </ha-card>
        <ha-card header="Discovery">
           <div class="card-content">
             <p>Scan for new sources and speakers on the network.</p>
           </div>
           <div class="card-actions">
             <mwc-button id="scan-btn">Scan for Devices</mwc-button>
           </div>
        </ha-card>

        <ha-card header="Bluetooth">
          <div class="card-content">
            <p>Manage Bluetooth pairing and connected devices.</p>
            <div id="bt-status" class="details"></div>
          </div>
          <div class="card-actions">
            <mwc-button id="bt-pair-btn">Enter Pairing Mode</mwc-button>
          </div>
        </ha-card>
      </div>
    `;
    this._setupEventListeners();
  }

  _updateUI() {
    if (!this._state) return;
    const { mediaPlayer } = this._state;

    const statusTag = this.shadowRoot.getElementById('session-status');
    const sessionText = this.shadowRoot.getElementById('session-text');
    const sessionDetails = this.shadowRoot.getElementById('session-details');
    const recoverBtn = this.shadowRoot.getElementById('recover-btn');

    if (mediaPlayer && mediaPlayer.attributes) {
        const attr = mediaPlayer.attributes;
        const presState = attr.presentation_state || 'idle';
        const detail = attr.presentation_detail || '';
        const streamProfile = attr.effective_stream_profile || '';
        const lastError = attr.last_error;

        statusTag.textContent = presState;
        statusTag.className = `status-tag status-${presState}`;

        if (presState === 'playing') {
            sessionText.textContent = attr.media_title || `Streaming to speakers`;
            sessionDetails.textContent = `Profile: ${streamProfile}`;
            recoverBtn.style.display = 'none';
        } else if (presState === 'idle' && detail.includes('detached')) {
            sessionText.textContent = `Playback Detached (Idle)`;
            sessionDetails.textContent = `Detail: ${detail}`;
            recoverBtn.style.display = 'none';
        } else if (presState === 'error' || attr.bridge_state === 'failed') {
            sessionText.textContent = `Playback Failed`;
            sessionDetails.textContent = lastError ? (lastError.action || lastError.message) : 'Unknown error';
            recoverBtn.style.display = '';
            statusTag.textContent = 'error';
            statusTag.className = 'status-tag status-error';
        } else if (presState === 'buffering') {
            sessionText.textContent = `Starting playback...`;
            sessionDetails.textContent = '';
            recoverBtn.style.display = 'none';
        } else {
            sessionText.textContent = `Ready to play`;
            sessionDetails.textContent = '';
            recoverBtn.style.display = 'none';
        }
    } else {
        statusTag.textContent = 'idle';
        statusTag.className = 'status-tag status-idle';
        sessionText.textContent = 'No active playback';
        sessionDetails.textContent = '';
        recoverBtn.style.display = 'none';
    }
  }

  _setupEventListeners() {
    this.shadowRoot.getElementById('play-btn').onclick = () => this._handlePlay();
    this.shadowRoot.getElementById('stop-btn').onclick = () => this._handleStop();
    this.shadowRoot.getElementById('recover-btn').onclick = () => this._handleRecover();
    this.shadowRoot.getElementById('scan-btn').onclick = () => this._handleScan();
    this.shadowRoot.getElementById('bt-pair-btn').onclick = () => this._handleBTPair();
  }

  _handlePlay() {
    if (this._state.mediaPlayer) {
        this._hass.callService('media_player', 'media_play', { entity_id: this._state.mediaPlayer.entity_id });
    }
  }

  _handleStop() {
    if (this._state.mediaPlayer) {
        this._hass.callService('media_player', 'media_stop', { entity_id: this._state.mediaPlayer.entity_id });
    }
  }

  _handleRecover() {
    this._hass.callService('ikea_symfonisk_gateway', 'recover_playback', {});
  }

  _handleScan() {
    this._hass.callService('ikea_symfonisk_gateway', 'refresh_discovery', {});
  }

  _handleBTPair() {
    this._hass.callService('ikea_symfonisk_gateway', 'open_pairing_window', { timeout_seconds: 90 });
  }
}

customElements.define('symfonisk-gateway-panel', SymfoniskGatewayPanel);
