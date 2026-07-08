import sys

filepath = r"C:\Users\ronsi95openclaw\Claude-openclaw\dashboard\app.py"

with open(filepath, "r", encoding="utf-8") as f:
    content = f.read()

START_MARKER = 'DASHBOARD_HTML = """'
END_MARKER = '</html>"""'

start_idx = content.find(START_MARKER)
if start_idx == -1:
    print("ERROR: START_MARKER not found")
    sys.exit(1)

end_search_from = start_idx + len(START_MARKER)
end_idx = content.find(END_MARKER, end_search_from)
if end_idx == -1:
    print("ERROR: END_MARKER not found")
    sys.exit(1)

end_idx += len(END_MARKER)  # include the closing """

old_block = content[start_idx:end_idx]
print(f"Found block: lines {content[:start_idx].count(chr(10))+1} to {content[:end_idx].count(chr(10))+1}")
print(f"Block length: {len(old_block)} chars")

NEW_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Command Center</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Share+Tech+Mono&display=swap" rel="stylesheet">
<style>
  :root {
    --neon:#00ff88;--neon2:#00ffff;--amber:#ffaa00;--red:#ff4455;
    --pink:#ff00aa;--purple:#9b59b6;--orange:#ff6b35;
    --bg:#080808;--bg2:#0f0f0f;--card:#0f0f0f;--border:#1e1e1e;
    --text:#c8c8c8;--muted:#555;
  }
  *{box-sizing:border-box;margin:0;padding:0;}
  body{
    background:var(--bg);color:var(--text);
    font-family:'Share Tech Mono',monospace;font-size:13px;
    background-image:radial-gradient(circle,#181818 1px,transparent 1px);
    background-size:22px 22px;background-attachment:fixed;
    padding-bottom:160px;
  }
  /* HEADER */
  .hdr{
    background:#060606;border-bottom:2px solid var(--neon);
    box-shadow:0 0 20px #00ff8822;padding:0 20px;height:54px;
    display:flex;align-items:center;gap:14px;
    position:sticky;top:0;z-index:500;
  }
  .hdr-title{
    font-family:'Press Start 2P',monospace;font-size:10px;
    color:var(--neon);text-shadow:0 0 12px var(--neon);
    letter-spacing:2px;white-space:nowrap;
  }
  .hdr-status{
    font-family:'Press Start 2P',monospace;font-size:7px;
    padding:4px 9px;border:1px solid var(--neon);
    color:var(--neon);background:#001a00;
    animation:blink 1.4s step-end infinite;
  }
  .hdr-status.idle{animation:none;opacity:0.5;border-color:#555;color:#555;}
  @keyframes blink{50%{opacity:0;}}
  .hdr-nav{display:flex;gap:0;margin-left:auto;}
  .hdr-nav a{
    color:var(--muted);text-decoration:none;font-size:10px;
    padding:4px 10px;border-left:1px solid var(--border);
    transition:color 0.2s,background 0.2s;
  }
  .hdr-nav a:hover{color:var(--neon);background:#001a00;}
  .hdr-clock{
    font-family:'Press Start 2P',monospace;font-size:8px;
    color:var(--neon2);min-width:72px;text-align:right;
    border-left:1px solid var(--border);padding-left:12px;
  }
  #refresh-bar{
    position:fixed;top:0;left:0;height:2px;width:0%;
    background:var(--neon);z-index:9999;transition:width 1s linear;
  }
  /* CMD BAR */
  .cmd-bar{
    background:#080808;border-bottom:1px solid var(--border);
    padding:8px 20px;display:flex;gap:8px;flex-wrap:wrap;align-items:center;
  }
  .cmd-lbl{font-family:'Press Start 2P',monospace;font-size:6px;color:var(--muted);letter-spacing:2px;}
  .cmd-btn{
    background:var(--card);border:1px solid var(--border);
    color:var(--neon);font-family:'Share Tech Mono',monospace;
    font-size:11px;padding:5px 12px;cursor:pointer;position:relative;
    transition:border-color 0.15s,box-shadow 0.15s;
  }
  .cmd-btn:hover{border-color:var(--neon);box-shadow:0 0 8px #00ff8833;}
  .cmd-btn .tip{
    position:absolute;bottom:110%;left:50%;transform:translateX(-50%);
    background:var(--neon);color:#000;font-size:8px;padding:2px 6px;
    white-space:nowrap;opacity:0;pointer-events:none;transition:opacity 0.2s;
  }
  .cmd-btn.copied .tip{opacity:1;}
  /* SECTION HDR */
  .sec-hdr{
    font-family:'Press Start 2P',monospace;font-size:7px;
    color:var(--muted);letter-spacing:3px;
    padding:18px 20px 8px;border-bottom:1px solid var(--border);
    margin-bottom:16px;
  }
  .sec-hdr em{color:var(--neon2);font-style:normal;}
  /* AGENT GRID */
  .agents-grid{
    display:grid;grid-template-columns:repeat(3,1fr);
    gap:16px;padding:16px 20px;
  }
  @media(max-width:900px){.agents-grid{grid-template-columns:repeat(2,1fr);}}
  @media(max-width:560px){.agents-grid{grid-template-columns:1fr;}}
  .agent-card{
    background:var(--card);border:1px solid;
    padding:16px;position:relative;
    transition:box-shadow 0.2s;
  }
  .agent-card:hover{box-shadow:0 0 18px currentColor;}
  .agent-card::after{
    content:'';position:absolute;top:0;left:0;right:0;
    height:2px;background:currentColor;opacity:0.5;
  }
  .agent-top{display:flex;align-items:center;gap:10px;margin-bottom:12px;}
  .agent-emoji{font-size:22px;line-height:1;}
  .agent-name{font-family:'Press Start 2P',monospace;font-size:8px;letter-spacing:1px;}
  .agent-role{font-size:10px;color:var(--muted);margin-top:3px;}
  .agent-badge{
    margin-left:auto;font-family:'Press Start 2P',monospace;
    font-size:6px;padding:3px 7px;border:1px solid currentColor;
  }
  .agent-badge.active{animation:blink 2s step-end infinite;}
  .agent-badge.idle{animation:none;opacity:0.4;}
  .hp-lbl{
    font-family:'Press Start 2P',monospace;font-size:6px;
    color:var(--muted);margin-bottom:4px;
    display:flex;justify-content:space-between;
  }
  .hp-track{
    background:#141414;height:8px;border:1px solid #222;
    margin-bottom:10px;overflow:hidden;position:relative;
  }
  .hp-fill{height:100%;transition:width 0.6s ease;position:relative;}
  .hp-fill::after{
    content:'';position:absolute;inset:0;
    background:repeating-linear-gradient(
      90deg,transparent 0px,transparent 4px,
      rgba(0,0,0,0.25) 4px,rgba(0,0,0,0.25) 5px
    );
  }
  .agent-stats{font-size:10px;color:var(--muted);}
  .agent-stats span{color:var(--text);}
  /* STATUS CARDS */
  .status-grid{
    display:grid;grid-template-columns:repeat(3,1fr);
    gap:16px;padding:0 20px 16px;
  }
  @media(max-width:768px){.status-grid{grid-template-columns:1fr;}}
  .status-card{background:var(--card);border:1px solid var(--border);padding:16px;}
  .status-card-title{
    font-family:'Press Start 2P',monospace;font-size:7px;
    color:var(--neon2);letter-spacing:2px;margin-bottom:12px;
    border-bottom:1px solid var(--border);padding-bottom:8px;
  }
  .sr{
    display:flex;justify-content:space-between;align-items:center;
    padding:4px 0;border-bottom:1px solid #111;font-size:11px;
  }
  .sr:last-child{border-bottom:none;}
  .sk{color:var(--muted);}
  .sv{color:var(--text);}
  .sv.ok{color:var(--neon);}
  .sv.warn{color:var(--amber);}
  .sv.err{color:var(--red);}
  /* PRICES */
  .pr{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #111;font-size:11px;}
  .pr:last-child{border-bottom:none;}
  .pc{color:var(--neon2);font-family:'Press Start 2P',monospace;font-size:7px;}
  .pv{font-size:12px;}
  .up{color:var(--neon);}
  .dn{color:var(--red);}
  /* TABLE */
  .section-wrap{padding:0 20px 20px;}
  .rtable{width:100%;border-collapse:collapse;font-size:11px;}
  .rtable th{
    font-family:'Press Start 2P',monospace;font-size:6px;
    color:var(--neon);letter-spacing:1px;padding:8px;
    border-bottom:1px solid var(--neon);text-align:left;background:#060606;
  }
  .rtable td{padding:6px 8px;border-bottom:1px solid var(--border);color:var(--text);}
  .rtable tr:hover td{background:#121212;}
  .bb{color:var(--neon);background:#00ff8811;border:1px solid #00ff8833;padding:1px 6px;font-size:10px;}
  .bs{color:var(--red);background:#ff445511;border:1px solid #ff445533;padding:1px 6px;font-size:10px;}
  /* CHAT */
  .chat-panel{
    position:fixed;bottom:0;right:20px;width:360px;
    background:#080808;border:1px solid var(--neon);
    border-bottom:none;box-shadow:0 0 20px #00ff8822;z-index:400;
  }
  .chat-hdr{
    background:#0a120a;border-bottom:1px solid var(--neon);
    padding:10px 14px;display:flex;align-items:center;gap:8px;cursor:pointer;
  }
  .chat-title{font-family:'Press Start 2P',monospace;font-size:7px;color:var(--neon);flex:1;}
  .chat-msgs{max-height:280px;overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:6px;}
  .msg-u{align-self:flex-end;background:#0a1a0a;border:1px solid #00ff8833;padding:6px 10px;font-size:11px;max-width:85%;white-space:pre-wrap;word-break:break-word;}
  .msg-b{background:#111;border:1px solid var(--border);padding:6px 10px;font-size:11px;max-width:85%;white-space:pre-wrap;word-break:break-word;}
  .chat-in-row{padding:8px 10px;border-top:1px solid var(--border);display:flex;gap:6px;}
  .chat-in-row input{
    flex:1;background:#0a0a0a;border:1px solid var(--border);
    color:var(--neon);font-family:'Share Tech Mono',monospace;
    font-size:11px;padding:6px 10px;outline:none;
  }
  .chat-in-row input:focus{border-color:var(--neon);}
  .chat-send{
    background:#0a120a;border:1px solid var(--neon);
    color:var(--neon);font-family:'Press Start 2P',monospace;
    font-size:8px;padding:6px 10px;cursor:pointer;
  }
  .chat-send:hover{background:var(--neon);color:#000;}
  @media(max-width:560px){
    .chat-panel{width:100%;right:0;left:0;}
    body{padding-bottom:200px;}
  }
</style>
</head>
<body>
<div id="refresh-bar"></div>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-title">&#9670; OPENCLAW-CMD &#9670;</div>
  <div class="hdr-status {% if not bot.running %}idle{% endif %}">
    {% if bot.running %}&#9679; ONLINE{% else %}&#9675; IDLE{% endif %}
  </div>
  <div class="hdr-nav">
    <a href="/taskboard">TASKS</a>
    <a href="/team">TEAM</a>
    <a href="/portfolio">PORTFOLIO</a>
    <a href="/holdings">HOLDINGS</a>
    <a href="/clip-economy">CASHCLAW</a>
  </div>
  <div class="hdr-clock" id="live-clock">00:00:00</div>
</div>

<!-- QUICK COMMANDS -->
<div class="cmd-bar">
  <span class="cmd-lbl">CMD:</span>
  {% for cmd in ['/scan','/market','/fng','/status','/cashclaw','/scout run','/autotrade on'] %}
  <button class="cmd-btn" onclick="copyCmd(this,'{{ cmd }}')"><span class="tip">COPIED</span>{{ cmd }}</button>
  {% endfor %}
  <span style="margin-left:auto;font-family:'Press Start 2P',monospace;font-size:6px;color:var(--muted);">REFRESH <span id="cd">30</span>s</span>
</div>

<!-- AGENTS -->
<div class="sec-hdr">&#9672; AGENT <em>STATUS</em> &#8212; {{ now }}</div>
<div class="agents-grid">

{% set j_hp = [100, (usage.ollama_calls + usage.claude_calls) * 4 + 30] | min %}
<div class="agent-card" style="border-color:#00ff88;color:#00ff88;">
  <div class="agent-top">
    <div class="agent-emoji">&#129504;</div>
    <div>
      <div class="agent-name" style="color:#00ff88;">JARVIS</div>
      <div class="agent-role">Brain &middot; {{ ollama.active }}</div>
    </div>
    <div class="agent-badge {% if bot.running %}active{% else %}idle{% endif %}" style="color:#00ff88;">
      {% if bot.running %}ACTIVE{% else %}IDLE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ [j_hp, 30] | max }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ [j_hp,30]|max }}%;background:#00ff88;box-shadow:0 0 6px #00ff88;"></div></div>
  <div class="agent-stats">
    ollama: <span>{{ usage.ollama_calls }}</span> &nbsp;
    claude: <span>{{ usage.claude_calls }}</span> &nbsp;
    cache: <span>{{ usage.cache_hits }}</span>
  </div>
</div>

<div class="agent-card" style="border-color:#00ffff;color:#00ffff;">
  <div class="agent-top">
    <div class="agent-emoji">&#128269;</div>
    <div>
      <div class="agent-name" style="color:#00ffff;">SCOUT</div>
      <div class="agent-role">Job Scout &middot; Whop/Discord</div>
    </div>
    <div class="agent-badge active" style="color:#00ffff;">STANDBY</div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>60%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:60%;background:#00ffff;box-shadow:0 0 6px #00ffff;"></div></div>
  <div class="agent-stats">27 terms &middot; 5 categories &middot; 6h cycle</div>
</div>

{% set wd_hp = 90 if autotrade.enabled else 35 %}
<div class="agent-card" style="border-color:#ffaa00;color:#ffaa00;">
  <div class="agent-top">
    <div class="agent-emoji">&#128021;</div>
    <div>
      <div class="agent-name" style="color:#ffaa00;">WATCHDOG</div>
      <div class="agent-role">Auto-Trade &middot; RSI+MACD</div>
    </div>
    <div class="agent-badge {% if autotrade.enabled %}active{% else %}idle{% endif %}" style="color:#ffaa00;">
      {% if autotrade.enabled %}ARMED{% else %}SAFE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ wd_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ wd_hp }}%;background:#ffaa00;box-shadow:0 0 6px #ffaa00;"></div></div>
  <div class="agent-stats">
    trades: <span>{{ trades|length }}</span> &nbsp;
    {% if autotrade.enabled %}scan: <span>{{ autotrade.scan_time }} UTC</span>{% else %}<span>disabled</span>{% endif %}
  </div>
</div>

{% set cx_hp = 80 if codereview.date else 25 %}
<div class="agent-card" style="border-color:#9b59b6;color:#9b59b6;">
  <div class="agent-top">
    <div class="agent-emoji">&#9881;&#65039;</div>
    <div>
      <div class="agent-name" style="color:#9b59b6;">CODEX</div>
      <div class="agent-role">Code Review &middot; Auto-Upgrade</div>
    </div>
    <div class="agent-badge {% if codereview.date %}active{% else %}idle{% endif %}" style="color:#9b59b6;">
      {% if codereview.date %}ACTIVE{% else %}IDLE{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ cx_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ cx_hp }}%;background:#9b59b6;box-shadow:0 0 6px #9b59b6;"></div></div>
  <div class="agent-stats">
    {% if codereview.date %}last: <span>{{ codereview.date }}</span>{% else %}no reviews yet{% endif %}
    &nbsp; skills: <span>{{ skills|length }}</span>
  </div>
</div>

<div class="agent-card" style="border-color:#ff6b35;color:#ff6b35;">
  <div class="agent-top">
    <div class="agent-emoji">&#129438;</div>
    <div>
      <div class="agent-name" style="color:#ff6b35;">CLIPPER</div>
      <div class="agent-role">CashClaw &middot; HumanVoice</div>
    </div>
    <div class="agent-badge active" style="color:#ff6b35;">READY</div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>70%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:70%;background:#ff6b35;box-shadow:0 0 6px #ff6b35;"></div></div>
  <div class="agent-stats">
    <a href="/clip-economy" style="color:#ff6b35;text-decoration:none;">&#8594; income panel</a>
  </div>
</div>

{% set hk_hp = 85 if prices else 20 %}
<div class="agent-card" style="border-color:#ff00aa;color:#ff00aa;">
  <div class="agent-top">
    <div class="agent-emoji">&#129413;</div>
    <div>
      <div class="agent-name" style="color:#ff00aa;">HAWK</div>
      <div class="agent-role">Market Watch &middot; Prices</div>
    </div>
    <div class="agent-badge {% if prices %}active{% else %}idle{% endif %}" style="color:#ff00aa;">
      {% if prices %}LIVE{% else %}DARK{% endif %}
    </div>
  </div>
  <div class="hp-lbl"><span>HP</span><span>{{ hk_hp }}%</span></div>
  <div class="hp-track"><div class="hp-fill" style="width:{{ hk_hp }}%;background:#ff00aa;box-shadow:0 0 6px #ff00aa;"></div></div>
  <div class="agent-stats">
    {% if prices %}
      {% for coin, d in prices.items() %}<span style="color:#ff00aa;">{{ coin }}</span> ${{ "{:,.0f}".format(d.price) }} &nbsp;{% endfor %}
    {% else %}CoinGecko offline{% endif %}
  </div>
</div>

</div>

<!-- STATUS ROW -->
<div class="sec-hdr">&#9672; SYSTEM <em>OVERVIEW</em></div>
<div class="status-grid">

  <div class="status-card">
    <div class="status-card-title">SYSTEM</div>
    <div class="sr"><span class="sk">ClawBot</span>
      <span class="sv {% if bot.running %}ok{% else %}warn{% endif %}">{% if bot.running %}&#9679; ACTIVE ({{ bot.last_seen }}){% else %}&#9675; IDLE ({{ bot.last_seen }}){% endif %}</span></div>
    <div class="sr"><span class="sk">Ollama</span>
      <span class="sv {% if ollama.online %}ok{% else %}err{% endif %}">{% if ollama.online %}&#9679; {{ ollama.active }}{% else %}&#10007; OFFLINE{% endif %}</span></div>
    <div class="sr"><span class="sk">Claude API</span>
      <span class="sv {% if claude_ok %}ok{% else %}warn{% endif %}">{% if claude_ok %}&#9679; SET{% else %}&#9675; NOT SET{% endif %}</span></div>
    <div class="sr"><span class="sk">Crypto.com</span>
      <span class="sv {% if crypto_ok %}ok{% else %}warn{% endif %}">{% if crypto_ok %}&#9679; SET{% else %}&#9675; NOT SET{% endif %}</span></div>
    <div class="sr"><span class="sk">Cache</span>
      <span class="sv">{{ cache.entries }} entries &middot; {{ cache.newest }}</span></div>
    <div class="sr"><span class="sk">Auto-Trade</span>
      <span class="sv {% if autotrade.enabled %}ok{% else %}warn{% endif %}">{% if autotrade.enabled %}ENABLED{% else %}DISABLED{% endif %}</span></div>
  </div>

  <div class="status-card">
    <div class="status-card-title">LIVE PRICES</div>
    {% if prices %}
      {% for coin, d in prices.items() %}
      <div class="pr">
        <span class="pc">{{ coin }}</span>
        <span class="pv">${{ "{:,.2f}".format(d.price) }}</span>
        <span class="{% if d.change >= 0 %}up{% else %}dn{% endif %}">{{ d.sign }}{{ d.change }}%</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:11px;padding:8px 0;">CoinGecko unavailable</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">BRAIN TODAY</div>
    <div class="sr"><span class="sk">Ollama</span><span class="sv ok">{{ usage.ollama_calls }} calls</span></div>
    <div class="sr"><span class="sk">Claude</span>
      <span class="sv {% if usage.claude_calls > 0 %}warn{% else %}ok{% endif %}">{{ usage.claude_calls }} calls</span></div>
    <div class="sr"><span class="sk">Cache hits</span><span class="sv ok">{{ usage.cache_hits }}</span></div>
    <div class="sr"><span class="sk">Tokens in</span>
      <span class="sv">{{ "{:,}".format(usage.claude_input_tokens) }}</span></div>
    {% set cost = (usage.claude_input_tokens * 0.00000025) + (usage.claude_output_tokens * 0.00000125) %}
    <div class="sr"><span class="sk">API cost</span>
      <span class="sv {% if cost > 0.01 %}warn{% else %}ok{% endif %}">${{ "%.4f"|format(cost) }}</span></div>
    <div class="sr"><span class="sk">Model</span>
      <span class="sv" style="font-size:10px;">{{ ollama.active }}</span></div>
  </div>

</div>

<!-- DATA FEEDS -->
<div class="sec-hdr">&#9672; DATA <em>FEEDS</em></div>
<div class="status-grid">

  <div class="status-card">
    <div class="status-card-title">REMINDERS</div>
    {% if tasks %}
      {% for t in tasks[:5] %}
      <div class="sr">
        <span class="sk">{{ t.time }}</span>
        <span class="sv" style="font-size:10px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ t.text[:38] }}{% if t.text|length > 38 %}&hellip;{% endif %}</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No reminders. /remind HH:MM text</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">KNOWLEDGE BASE &middot; {{ notes.count }}</div>
    {% if notes.count > 0 %}
      {% for n in notes.recent[:4] %}
      <div class="sr">
        <span class="sk" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:65%;">{{ n.title[:32] }}</span>
        <span class="sv" style="color:#444;font-size:10px;">{{ n.timestamp[:10] }}</span>
      </div>
      {% endfor %}
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No notes. /save in Telegram.</div>
    {% endif %}
  </div>

  <div class="status-card">
    <div class="status-card-title">BACKTEST</div>
    {% if backtest and backtest.ranking %}
      <div class="sr"><span class="sk">Strategy</span><span class="sv ok">{{ backtest.top_strategy }}</span></div>
      <div class="sr"><span class="sk">Pair</span><span class="sv">{{ backtest.top_pair }}</span></div>
      <div class="sr"><span class="sk">Return</span>
        <span class="sv {% if backtest.top_return > 0 %}ok{% else %}err{% endif %}">{{ "%+.0f"|format(backtest.top_return) }}%</span></div>
      <div class="sr"><span class="sk">Win rate</span><span class="sv">{{ backtest.top_winrate }}%</span></div>
      <div class="sr"><span class="sk">Generated</span><span class="sv" style="color:#444;">{{ backtest.generated }}</span></div>
    {% else %}
      <div style="color:var(--muted);font-size:10px;padding:8px 0;">No data. /backtest run</div>
    {% endif %}
  </div>

</div>

<!-- TRADE LOG -->
<div class="sec-hdr">&#9672; TRADE <em>LOG</em> ({{ trades|length }} entries)</div>
<div class="section-wrap">
  {% if trades %}
  <div style="overflow-x:auto;">
    <table class="rtable">
      <thead>
        <tr><th>TIME</th><th>COIN</th><th>ACTION</th><th>USD</th><th>STATUS</th><th>NOTES</th></tr>
      </thead>
      <tbody>
        {% for t in trades|reverse %}
        <tr>
          <td style="color:var(--muted);">{{ t.get('timestamp','')[:16]|replace('T',' ') }}</td>
          <td style="color:var(--neon2);">{{ t.get('coin','?') }}</td>
          <td>{% set act=t.get('action','') %}<span class="{% if act=='BUY' %}bb{% else %}bs{% endif %}">{{ act or '&mdash;' }}</span></td>
          <td style="font-family:monospace;">${{ "%.2f"|format(t.get('usd_amount',0)|float) }}</td>
          <td>{% set st=t.get('status','') %}<span style="color:{% if st=='executed' %}var(--neon){% elif st=='error' %}var(--red){% else %}var(--amber){% endif %};">{{ st or '&mdash;' }}</span></td>
          <td style="color:var(--muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{{ t.get('reason',t.get('notes',''))[:50] }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div style="font-family:'Press Start 2P',monospace;font-size:8px;color:var(--muted);padding:16px 0;">
    NO TRADES LOGGED &mdash; /autotrade on
  </div>
  {% endif %}
</div>

<!-- CHAT PANEL -->
<div class="chat-panel" id="chat-panel">
  <div class="chat-hdr" onclick="toggleChat()">
    <span style="color:var(--neon);">&#9658;</span>
    <span class="chat-title">CLAWBOT CHAT</span>
    <span id="brain-badge" style="font-size:9px;color:var(--muted);"></span>
    <button onclick="clearChat(event)" style="background:none;border:1px solid var(--muted);color:var(--muted);font-size:9px;padding:2px 7px;cursor:pointer;font-family:'Share Tech Mono',monospace;margin-left:8px;">CLR</button>
    <span id="chat-toggle-icon" style="font-size:10px;color:var(--muted);margin-left:6px;">&#9650;</span>
  </div>
  <div id="chat-messages" class="chat-msgs">
    <div class="msg-b">CLAWBOT ONLINE &#8212; what's the move, Ronnie?</div>
  </div>
  <div class="chat-in-row" id="chat-input-row">
    <input id="chat-input" type="text" placeholder="> ask clawbot..."
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){sendChat();event.preventDefault();}">
    <button class="chat-send" onclick="sendChat()" id="send-btn">&#9658;</button>
  </div>
</div>

<script>
// Clock
function updateClock(){
  const n=new Date(),p=s=>String(s).padStart(2,'0');
  document.getElementById('live-clock').textContent=p(n.getHours())+':'+p(n.getMinutes())+':'+p(n.getSeconds());
}
updateClock();setInterval(updateClock,1000);

// Auto-refresh
const RS=30;let t=RS,chatActive=false;
const el=document.getElementById('cd'),bar=document.getElementById('refresh-bar');
function upBar(){bar.style.width=((RS-t)/RS*100)+'%';}upBar();
setInterval(()=>{
  if(chatActive){t=RS;el.textContent=t;upBar();return;}
  t--;el.textContent=t;upBar();if(t<=0)location.reload();
},1000);

// Copy commands
function copyCmd(btn,cmd){
  navigator.clipboard.writeText(cmd).catch(()=>{});
  btn.classList.add('copied');setTimeout(()=>btn.classList.remove('copied'),1200);
}

// Chat
let chatOpen=true;
const msgs=document.getElementById('chat-messages');
const inp=document.getElementById('chat-input');
const CK='clawbot_chat_history';

function _save(){
  const items=[];
  msgs.querySelectorAll('div[data-role]').forEach(d=>{
    items.push({role:d.dataset.role,text:d.dataset.text,brain:d.dataset.brain||''});
  });
  localStorage.setItem(CK,JSON.stringify(items.slice(-20)));
}
function _restore(){
  try{JSON.parse(localStorage.getItem(CK)||'[]').forEach(m=>_add(m.text,m.role,m.brain));}catch(e){}
}
_restore();

function toggleChat(){
  chatOpen=!chatOpen;
  document.getElementById('chat-messages').style.display=chatOpen?'flex':'none';
  document.getElementById('chat-input-row').style.display=chatOpen?'flex':'none';
  document.getElementById('chat-toggle-icon').textContent=chatOpen?'\u25B2':'\u25BC';
}
function _add(text,type,brain){
  const d=document.createElement('div');
  d.className=type==='user'?'msg-u':'msg-b';
  d.dataset.role=type;d.dataset.text=text;d.dataset.brain=brain||'';
  d.textContent=text;
  if(brain&&type==='bot'){
    const b=document.createElement('span');
    b.textContent=' ('+(brain)+')';b.style.cssText='color:#555;font-size:9px;';d.appendChild(b);
    document.getElementById('brain-badge').textContent=brain;
  }
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;return d;
}
async function sendChat(){
  const text=inp.value.trim();if(!text)return;
  inp.value='';inp.disabled=true;
  document.getElementById('send-btn').disabled=true;chatActive=true;
  _add(text,'user','');_save();
  const th=_add('...','bot','');
  try{
    const res=await fetch('/api/chat',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:text})});
    const data=await res.json();
    if(data.error){th.textContent='ERROR: '+data.error;th.style.color='var(--red)';}
    else{
      th.textContent=data.reply;th.dataset.text=data.reply;th.dataset.brain=data.brain||'';
      const b=document.createElement('span');b.textContent=' ('+(data.brain||'')+')';
      b.style.cssText='color:#555;font-size:9px;';th.appendChild(b);
      document.getElementById('brain-badge').textContent=data.brain||'';
    }
    _save();
  }catch(e){th.textContent='CONNECTION ERROR';th.style.color='var(--red)';}
  inp.disabled=false;document.getElementById('send-btn').disabled=false;
  chatActive=false;inp.focus();
}
async function clearChat(e){
  e.stopPropagation();
  await fetch('/api/chat/clear',{method:'POST'});
  localStorage.removeItem(CK);
  while(msgs.children.length>1)msgs.removeChild(msgs.lastChild);
  document.getElementById('brain-badge').textContent='';
}
inp.focus();
</script>
</body>
</html>"""

new_block = 'DASHBOARD_HTML = """' + NEW_HTML

new_content = content[:start_idx] + new_block + content[end_idx:]

with open(filepath, "w", encoding="utf-8") as f:
    f.write(new_content)

print("Done. File written successfully.")
print(f"New block length: {len(new_block)} chars")
