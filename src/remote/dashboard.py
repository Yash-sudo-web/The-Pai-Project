"""Local web dashboard for the Personal AI Assistant."""

from __future__ import annotations


def render_dashboard_html() -> str:
    """Return the self-contained dashboard HTML."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Personal AI Assistant</title>
  
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Outfit:wght@500;600;700;800&display=swap" rel="stylesheet">
  
  <style>
    :root {
      --bg-base: #0b0a10;
      --bg-glass: rgba(30, 26, 42, 0.4);
      --bg-panel: rgba(22, 20, 28, 0.85);
      --bg-input: rgba(18, 16, 24, 0.9);
      
      --accent-cyan: #00f0ff;
      --accent-purple: #b026ff;
      --accent-blue: #3b82f6;
      
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      
      --line: rgba(255, 255, 255, 0.08);
      
      --radius-sm: 12px;
      --radius-md: 20px;
      --radius-lg: 32px;
      
      --danger: #ef4444;
      --ok: #10b981;
      --warn: #f59e0b;
    }

    * { box-sizing: border-box; }
    
    html, body {
      margin: 0;
      padding: 0;
      height: 100vh;
      overflow: hidden;
      color: var(--text-main);
      font-family: 'Inter', system-ui, sans-serif;
      background-color: var(--bg-base);
    }

    /* Ambient Background Glows */
    .bg-orbs {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      z-index: -1;
      overflow: hidden;
      pointer-events: none;
    }
    .orb {
      position: absolute;
      border-radius: 50%;
      filter: blur(120px);
      opacity: 0.35;
      animation: drift 25s infinite alternate ease-in-out;
    }
    .orb-1 { top: -10vh; left: -10vw; width: 600px; height: 600px; background: var(--accent-purple); }
    .orb-2 { bottom: -20vh; right: -5vw; width: 700px; height: 700px; background: var(--accent-cyan); animation-delay: -5s; }
    .orb-3 { top: 40vh; left: 30vw; width: 400px; height: 400px; background: var(--accent-blue); opacity: 0.2; animation-delay: -15s; }

    @keyframes drift {
      0% { transform: translate(0, 0) scale(1); }
      100% { transform: translate(80px, -60px) scale(1.1); }
    }

    .app-container {
      display: grid;
      grid-template-columns: 260px 1fr 400px;
      gap: 24px;
      height: 100vh;
      padding: 24px;
      max-width: 1800px;
      margin: 0 auto;
    }

    /* ---------------------------------
       LEFT SIDEBAR
    ----------------------------------*/
    .sidebar {
      display: flex;
      flex-direction: column;
      padding: 12px;
    }

    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      font-family: 'Outfit', sans-serif;
      font-size: 1.4rem;
      font-weight: 700;
      letter-spacing: 0.05em;
      margin-bottom: 40px;
      padding: 0 16px;
    }
    .logo-icon {
      color: var(--accent-purple);
    }

    .nav-items {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }
    
    .nav-item {
      padding: 14px 20px;
      border-radius: var(--radius-sm);
      color: var(--text-muted);
      font-weight: 500;
      font-size: 0.95rem;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 14px;
      transition: all 0.2s ease;
    }
    .nav-item:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.05); }
    .nav-item.active { background: rgba(255, 255, 255, 0.1); color: var(--text-main); }
    
    /* Config form embedded in sidebar for API key */
    .auth-config {
      margin-top: auto;
      padding: 20px 16px;
    }
    .auth-config label {
      display: block;
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--text-muted);
      margin-bottom: 8px;
    }
    .auth-config input {
      width: 100%;
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--line);
      color: var(--text-main);
      padding: 12px;
      border-radius: var(--radius-sm);
      font-family: monospace;
      font-size: 0.85rem;
    }
    .auth-config input:focus {
      outline: none;
      border-color: var(--accent-purple);
    }

    /* ---------------------------------
       CENTER VOICE HUB
    ----------------------------------*/
    .center-panel {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
    }

    .header-text {
      position: absolute;
      top: 40px;
      text-align: center;
    }
    .header-text h1 {
      font-family: 'Outfit', sans-serif;
      font-size: 2.2rem;
      font-weight: 600;
      margin: 0;
      background: linear-gradient(90deg, #fff, #a5b4fc);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .mic-hub {
      position: relative;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .mic-button {
      width: 180px;
      height: 180px;
      border-radius: 50%;
      background: linear-gradient(135deg, var(--bg-panel), #1a1525);
      border: 3px solid rgba(0, 240, 255, 0.5);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      box-shadow: 0 0 50px rgba(0, 240, 255, 0.15), inset 0 0 20px rgba(0,0,0,0.5);
      transition: all 0.3s ease;
      z-index: 10;
    }
    
    .mic-button:hover {
      transform: scale(1.05);
      box-shadow: 0 0 70px rgba(0, 240, 255, 0.3), inset 0 0 30px rgba(0,0,0,0.5);
    }
    .mic-button:active {
      transform: scale(0.95);
    }
    
    .mic-button svg {
      width: 72px;
      height: 72px;
      color: var(--accent-cyan);
      transition: all 0.3s ease;
    }

    /* Rings for animation */
    .ring {
      position: absolute;
      border-radius: 50%;
      border: 2px solid var(--accent-cyan);
      pointer-events: none;
      opacity: 0;
    }

    /* Recording State */
    .mic-hub.recording .mic-button {
      border-color: var(--accent-purple);
      box-shadow: 0 0 80px rgba(176, 38, 255, 0.5), inset 0 0 40px rgba(0,0,0,0.5);
      animation: pulseMic 1.5s infinite alternate;
    }
    .mic-hub.recording .mic-button svg {
      color: var(--accent-purple);
      /* change icon to stop block or just keep it colored */
    }
    .mic-hub.recording .ring {
      animation: ripple 2s linear infinite;
    }
    .mic-hub.recording .ring:nth-child(2) {
      animation-delay: 1s;
    }
    
    @keyframes pulseMic {
      0% { transform: scale(1); }
      100% { transform: scale(1.06); }
    }
    
    @keyframes ripple {
      0% { width: 180px; height: 180px; opacity: 1; border-color: var(--accent-purple); }
      100% { width: 450px; height: 450px; opacity: 0; border-color: var(--accent-purple); border-width: 8px; }
    }

    /* Processing State (after recording stops, awaiting API) */
    .mic-hub.processing .mic-button {
      border-color: var(--warn);
      box-shadow: 0 0 60px rgba(245, 158, 11, 0.4), inset 0 0 30px rgba(0,0,0,0.5);
      animation: spinBorder 2s linear infinite;
    }
    .mic-hub.processing .mic-button svg {
      color: var(--warn);
    }
    @keyframes spinBorder {
      0% { transform: rotate(0deg); }
      100% { transform: rotate(360deg); }
    }


    /* ---------------------------------
       RIGHT CHAT AREA
    ----------------------------------*/
    .main-view {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      position: relative;
    }

    .chat-container {
      flex: 1;
      min-height: 0;
      background: var(--bg-panel);
      backdrop-filter: blur(24px);
      border: 1px solid var(--line);
      border-radius: var(--radius-md);
      display: flex;
      flex-direction: column;
      overflow: hidden;
      box-shadow: 0 20px 50px rgba(0,0,0,0.5);
    }

    .chat-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 20px 24px;
      border-bottom: 1px solid var(--line);
      font-weight: 500;
      font-size: 0.95rem;
    }

    .chat-feed {
      flex: 1;
      overflow-y: auto;
      padding: 24px;
      display: flex;
      flex-direction: column;
      gap: 24px;
      scroll-behavior: smooth;
    }
    .chat-feed::-webkit-scrollbar { width: 6px; }
    .chat-feed::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 10px; }

    .message {
      display: flex;
      flex-direction: column;
      max-width: 90%;
      margin-bottom: 8px;
    }
    .message.assistant {
      align-self: flex-start;
    }
    .message.user {
      align-self: flex-end;
      align-items: flex-end;
    }

    .bubble-wrapper {
      display: flex;
      gap: 12px;
      align-items: center;
    }
    .message.user .bubble-wrapper { flex-direction: row-reverse; }

    .avatar {
      width: 32px;
      height: 32px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      background: rgba(255, 255, 255, 0.05);
      flex-shrink: 0;
    }
    .avatar.ai {
      background: rgba(176, 38, 255, 0.15);
      color: var(--accent-purple);
    }
    
    .bubble {
      padding: 14px 18px;
      border-radius: 16px;
      background: rgba(255, 255, 255, 0.03);
      font-size: 0.9rem;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
      animation: messageEnter 0.3s ease-out forwards;
    }

    .bubble.user-bubble {
      background: linear-gradient( var(--bg-input), var(--bg-input) ) padding-box,
                  linear-gradient( 135deg, var(--accent-cyan), var(--accent-purple) ) border-box;
      border: 2px solid transparent;
      box-shadow: 0 4px 15px rgba(0, 240, 255, 0.1);
    }

    .bubble.pending-bubble {
      border: 1px solid var(--warn);
      background: rgba(245, 158, 11, 0.1);
      color: #fed7aa;
    }

    .msg-time {
      font-size: 0.7rem;
      color: var(--text-muted);
      margin-top: 6px;
      margin-left: 44px;
    }
    .message.user .msg-time {
      margin-left: 0;
      margin-right: 44px;
    }

    @keyframes messageEnter {
      from { opacity: 0; transform: translateY(10px); }
      to { opacity: 1; transform: translateY(0); }
    }

    /* Pending Confirmation Widget inside chat area */
    .pending-widget {
      display: none;
      border: 1px dashed var(--warn);
      background: rgba(245, 158, 11, 0.05);
      border-radius: var(--radius-sm);
      padding: 16px;
      margin: 0 20px 20px 20px;
      flex-direction: column;
      gap: 12px;
      animation: messageEnter 0.3s ease;
      flex-shrink: 0;
    }
    .pending-widget.visible { display: flex; }
    
    .pending-btns {
      display: flex;
      gap: 8px;
    }
    .btn-approve, .btn-reject {
      flex: 1;
      padding: 10px;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.85rem;
      transition: all 0.2s;
    }
    .btn-approve { background: var(--ok); color: #000; }
    .btn-approve:hover { background: #34d399; }
    .btn-reject { background: var(--danger); color: #fff; }
    .btn-reject:hover { background: #f87171; }

    /* Input Area (Fallback) */
    .input-area {
      padding: 0 24px 24px 24px;
      background: transparent;
      flex-shrink: 0;
    }
    
    .input-wrapper {
      position: relative;
      display: flex;
      align-items: center;
      background: linear-gradient( var(--bg-input), var(--bg-input) ) padding-box,
                  linear-gradient( 90deg, var(--accent-cyan), var(--accent-purple) ) border-box;
      border: 2px solid transparent;
      border-radius: var(--radius-sm);
      box-shadow: 0 5px 20px rgba(0, 0, 0, 0.2);
      transition: box-shadow 0.3s ease;
    }
    .input-wrapper:focus-within {
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4), 0 0 20px rgba(0, 240, 255, 0.2);
    }

    #command {
      flex: 1;
      background: transparent;
      border: none;
      color: var(--text-main);
      padding: 14px 16px;
      font-size: 0.95rem;
      font-family: inherit;
      resize: none;
      height: 48px;
    }
    #command:focus { outline: none; }
    #command::placeholder { color: var(--text-muted); }

    .input-actions {
      display: flex;
      align-items: center;
      padding-right: 8px;
    }
    
    .icon-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 32px;
      height: 32px;
      border-radius: 8px;
      transition: all 0.2s;
    }
    .icon-btn:hover { background: rgba(255, 255, 255, 0.1); color: var(--text-main); }
    
    .send-btn {
      background: var(--accent-purple);
      color: white;
    }
    .send-btn:hover {
      background: #c34dff;
      color: white;
      box-shadow: 0 0 10px rgba(176, 38, 255, 0.6);
    }
    .send-btn.sending { opacity: 0.5; pointer-events: none; }

    /* Utility */
    .d-none { display: none !important; }

    /* ---------------------------------
       SESSIONS DRAWER OVERLAY
    ----------------------------------*/
    .drawer-overlay {
      position: fixed;
      inset: 0;
      z-index: 100;
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(4px);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.3s ease;
    }
    .drawer-overlay.open {
      opacity: 1;
      pointer-events: all;
    }

    .sessions-drawer {
      position: fixed;
      top: 0;
      left: 0;
      width: min(480px, 90vw);
      height: 100vh;
      z-index: 101;
      background: var(--bg-panel);
      border-right: 1px solid var(--line);
      backdrop-filter: blur(30px);
      transform: translateX(-100%);
      transition: transform 0.35s cubic-bezier(0.4, 0, 0.2, 1);
      display: flex;
      flex-direction: column;
      box-shadow: 10px 0 50px rgba(0, 0, 0, 0.6);
    }
    .sessions-drawer.open {
      transform: translateX(0);
    }

    .drawer-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 24px;
      border-bottom: 1px solid var(--line);
      flex-shrink: 0;
    }
    .drawer-header h2 {
      font-family: 'Outfit', sans-serif;
      font-size: 1.3rem;
      font-weight: 600;
      margin: 0;
      background: linear-gradient(90deg, var(--accent-cyan), var(--accent-purple));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .drawer-close {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--line);
      color: var(--text-muted);
      width: 36px;
      height: 36px;
      border-radius: 10px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
    }
    .drawer-close:hover {
      background: rgba(255, 255, 255, 0.1);
      color: var(--text-main);
    }

    .drawer-content {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
    }
    .drawer-content::-webkit-scrollbar { width: 6px; }
    .drawer-content::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 10px; }

    .session-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--line);
      border-radius: var(--radius-sm);
      padding: 18px;
      margin-bottom: 12px;
      cursor: pointer;
      transition: all 0.25s ease;
      position: relative;
      overflow: hidden;
    }
    .session-card::before {
      content: '';
      position: absolute;
      left: 0;
      top: 0;
      bottom: 0;
      width: 3px;
      background: linear-gradient(180deg, var(--accent-cyan), var(--accent-purple));
      opacity: 0;
      transition: opacity 0.25s;
    }
    .session-card:hover {
      background: rgba(255, 255, 255, 0.06);
      border-color: rgba(255, 255, 255, 0.15);
      transform: translateX(4px);
    }
    .session-card:hover::before {
      opacity: 1;
    }
    .session-card.active-session {
      border-color: var(--accent-cyan);
      background: rgba(0, 240, 255, 0.04);
    }
    .session-card.active-session::before {
      opacity: 1;
    }

    .session-date {
      font-family: 'Outfit', sans-serif;
      font-weight: 600;
      font-size: 1rem;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .session-date .session-badge {
      font-size: 0.65rem;
      font-family: 'Inter', sans-serif;
      font-weight: 500;
      padding: 2px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    .badge-active {
      background: rgba(16, 185, 129, 0.15);
      color: var(--ok);
      border: 1px solid rgba(16, 185, 129, 0.3);
    }
    .badge-closed {
      background: rgba(148, 163, 184, 0.1);
      color: var(--text-muted);
      border: 1px solid rgba(148, 163, 184, 0.2);
    }

    .session-meta {
      font-size: 0.8rem;
      color: var(--text-muted);
      margin-bottom: 10px;
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .session-meta svg {
      width: 14px;
      height: 14px;
      flex-shrink: 0;
    }

    .session-summary {
      font-size: 0.85rem;
      line-height: 1.6;
      color: rgba(248, 250, 252, 0.7);
      background: rgba(0, 0, 0, 0.2);
      padding: 12px;
      border-radius: 8px;
      border: 1px solid rgba(255, 255, 255, 0.04);
    }

    .no-summary {
      font-size: 0.8rem;
      color: var(--text-muted);
      font-style: italic;
    }

    .drawer-empty {
      text-align: center;
      padding: 60px 20px;
      color: var(--text-muted);
    }
    .drawer-empty svg {
      width: 48px;
      height: 48px;
      margin-bottom: 16px;
      opacity: 0.3;
    }
    .drawer-empty p {
      font-size: 0.9rem;
    }

    .session-messages-view {
      display: none;
      flex-direction: column;
      height: 100%;
    }
    .session-messages-view.visible {
      display: flex;
    }
    .sessions-list-view.hidden {
      display: none;
    }

    .back-btn {
      display: flex;
      align-items: center;
      gap: 8px;
      background: none;
      border: none;
      color: var(--accent-cyan);
      cursor: pointer;
      font-size: 0.85rem;
      font-weight: 500;
      padding: 12px 16px;
      border-bottom: 1px solid var(--line);
      transition: all 0.2s;
      flex-shrink: 0;
    }
    .back-btn:hover {
      background: rgba(0, 240, 255, 0.05);
    }

    .session-msg-list {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .session-msg-list::-webkit-scrollbar { width: 6px; }
    .session-msg-list::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.1); border-radius: 10px; }

    .history-msg {
      display: flex;
      gap: 10px;
      align-items: flex-start;
    }
    .history-msg.history-user {
      flex-direction: row-reverse;
    }

    .history-avatar {
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
      background: rgba(255, 255, 255, 0.05);
    }
    .history-avatar.ai {
      background: rgba(176, 38, 255, 0.15);
      color: var(--accent-purple);
    }

    .history-bubble {
      padding: 10px 14px;
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.03);
      font-size: 0.85rem;
      line-height: 1.5;
      max-width: 85%;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .history-user .history-bubble {
      background: rgba(0, 240, 255, 0.06);
      border: 1px solid rgba(0, 240, 255, 0.15);
    }
    .history-time {
      font-size: 0.65rem;
      color: var(--text-muted);
      margin-top: 4px;
      padding: 0 38px;
    }
    .history-user .history-time {
      text-align: right;
    }

    /* Responsive */
    @media (max-width: 1200px) {
      .app-container { grid-template-columns: 260px 1fr; }
    }
    @media (max-width: 900px) {
      .app-container { grid-template-columns: 1fr; padding: 12px; }
      .sidebar { display: none; }
    }
  </style>
</head>
<body>
  <div class="bg-orbs">
    <div class="orb orb-1"></div>
    <div class="orb orb-2"></div>
    <div class="orb orb-3"></div>
  </div>

  <div class="app-container">
    
    <!-- LEFT SIDEBAR -->
    <aside class="sidebar">
      <div class="logo">
        <svg class="logo-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L21 12l-5.813-1.912a2 2 0 0 1-1.275-1.275L12 3Z"/></svg>
        the PAI Project
      </div>
      
      <div class="nav-items">
        <div class="nav-item active">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>
          Dashboard
        </div>
        <div class="nav-item" id="historyBtn">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
          Chat History
        </div>
        <div class="nav-item" id="clearBtn">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
          Clear Chat
        </div>
      </div>
      
      <div class="auth-config">
        <label>API Key</label>
        <input type="password" id="apiKey" placeholder="Authentication Key" />
        <div id="status" class="msg-time" style="margin-left:0; margin-top:12px; color:var(--text-muted)">System Ready</div>
      </div>
    </aside>

    <!-- CENTER VOICE HUB -->
    <main class="center-panel">
      <div class="header-text">
        <h1>Good Evening.</h1>
      </div>
      <div class="mic-hub" id="mainMicHub">
        <div class="ring"></div>
        <div class="ring"></div>
        <button class="mic-button" id="mainVoiceBtn" title="Hold or Click to Speak">
          <svg id="micIcon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" y1="19" x2="12" y2="22"/>
          </svg>
        </button>
      </div>
    </main>

    <!-- RIGHT CHAT AREA -->
    <aside class="main-view">
      <div class="chat-container">
        <div class="chat-header">
          <span>Interaction Log</span>
          <div class="timestamp" id="liveTime" style="font-size:0.85rem; color:var(--text-muted)">Date/Time • Syncing</div>
        </div>
        
        <div class="chat-feed" id="feed">
          <div class="message assistant">
            <div class="bubble-wrapper">
              <div class="avatar ai">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
              </div>
              <div class="bubble">Welcome to the pai project. How can I help today?</div>
            </div>
            <div class="msg-time">Just now</div>
          </div>
        </div>

        <div id="pendingBox" class="widget-card pending-widget">
          <div class="widget-header" style="color:var(--text-main); font-weight:600; font-size:0.9rem;">Action Required</div>
          <div id="pendingMeta" style="font-size:0.85rem; color:var(--text-muted); line-height:1.4"></div>
          <div class="pending-btns">
            <button id="approveBtn" class="btn-approve">Approve</button>
            <button id="rejectBtn" class="btn-reject">Reject</button>
          </div>
        </div>

        <div class="input-area">
          <div class="input-wrapper">
            <input type="text" id="command" placeholder="ask pai anything... /cmds" autocomplete="off" />
            <div class="input-actions">
              <button id="sendBtn" class="icon-btn send-btn" title="Send">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>
              </button>
            </div>
          </div>
        </div>
      </div>
    </aside>

  </div>

  <!-- SESSIONS DRAWER -->
  <div class="drawer-overlay" id="drawerOverlay"></div>
  <div class="sessions-drawer" id="sessionsDrawer">
    <div class="drawer-header">
      <h2>Chat History</h2>
      <button class="drawer-close" id="drawerCloseBtn">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
      </button>
    </div>

    <!-- Sessions List -->
    <div class="drawer-content sessions-list-view" id="sessionsListView">
      <div id="sessionsListContent"></div>
    </div>

    <!-- Session Messages Detail -->
    <div class="session-messages-view" id="sessionMessagesView">
      <button class="back-btn" id="backToListBtn">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="15 18 9 12 15 6"/></svg>
        Back to sessions
      </button>
      <div class="session-msg-list" id="sessionMsgList"></div>
    </div>
  </div>

  <script>
    // Live Time Setup
    function updateClock() {
      const now = new Date();
      document.getElementById('liveTime').innerText = `${now.toLocaleDateString()} | ${now.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})}`;
    }
    setInterval(updateClock, 1000);
    updateClock();

    const apiKeyInput = document.getElementById("apiKey");
    const commandInput = document.getElementById("command");
    const sendBtn = document.getElementById("sendBtn");
    
    // Main Voice Elements
    const mainMicHub = document.getElementById("mainMicHub");
    const mainVoiceBtn = document.getElementById("mainVoiceBtn");
    const micIcon = document.getElementById("micIcon");

    const clearBtn = document.getElementById("clearBtn");
    const statusEl = document.getElementById("status");
    const feedEl = document.getElementById("feed");
    const pendingBox = document.getElementById("pendingBox");
    const pendingMeta = document.getElementById("pendingMeta");
    const approveBtn = document.getElementById("approveBtn");
    const rejectBtn = document.getElementById("rejectBtn");

    let activePendingId = null;
    let pollTimer = null;
    let mediaRecorder = null;
    let mediaStream = null;
    let audioChunks = [];
    let isListening = false;

    apiKeyInput.value = localStorage.getItem("pai_api_key") || "";

    function authHeaders() {
      const key = apiKeyInput.value.trim();
      if (!key) throw new Error("API Key required.");
      localStorage.setItem("pai_api_key", key);
      return {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${key}`
      };
    }

    function setStatus(msg) {
      statusEl.textContent = msg;
    }
    
    // Text-to-Speech via Server (Groq)
    let currentAudio = null;

    async function speakText(text) {
      if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
      }
      try {
        const response = await fetch("/voice/tts", {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({ text })
        });
        if (!response.ok) throw new Error("TTS request failed.");
        
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        currentAudio = new Audio(url);
        
        currentAudio.onplay = () => mainMicHub.classList.add("recording"); // Reuse pulsing animation
        currentAudio.onended = () => {
          mainMicHub.classList.remove("recording");
          URL.revokeObjectURL(url);
        };
        
        await currentAudio.play();
      } catch (err) {
        console.error("TTS Error:", err);
      }
    }

    function addBubble(role, text) {
      const msgDiv = document.createElement("div");
      msgDiv.className = `message ${role}`;
      
      const timeStr = new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
      
      let avatarHtml = `<div class="avatar ai"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></div>`;
      if (role === 'user') {
        avatarHtml = `<div class="avatar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>`;
      }
      if (role === 'pending') {
        avatarHtml = '';
      }

      msgDiv.innerHTML = `
        <div class="bubble-wrapper">
          ${avatarHtml}
          <div class="bubble ${role}-bubble">${text}</div>
        </div>
        <div class="msg-time">${timeStr}</div>
      `;
      
      feedEl.appendChild(msgDiv);
      feedEl.scrollTop = feedEl.scrollHeight;
    }

    function formatResult(result) {
      if (!result) return "No payload returned.";
      const lines = [];
      if (typeof result.message === "string") lines.push(result.message);
      if (result.clarification_question) lines.push(`🤔 ${result.clarification_question}`);
      if (result.rollback_warnings && result.rollback_warnings.length) {
        lines.push(`⚠️ Rollbacks: ${result.rollback_warnings.join("; ")}`);
      }
      return lines.join("\\n\\n") || JSON.stringify(result, null, 2);
    }

    function showPending(pendingId) {
      activePendingId = pendingId;
      pendingMeta.textContent = `Grant execution logic for Token: ${pendingId}`;
      pendingBox.classList.add("visible");
      // Scroll to pending box
      feedEl.scrollTop = feedEl.scrollHeight;
    }

    function hidePending() {
      activePendingId = null;
      pendingBox.classList.remove("visible");
      pendingMeta.textContent = "";
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
    }

    async function pollStatus(pendingId) {
      try {
        const response = await fetch(`/status/${pendingId}`, { headers: authHeaders() });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Polling failed.");

        if (data.status === "completed") {
          hidePending();
          const textResponse = formatResult(data.result);
          addBubble("assistant", textResponse);
          setStatus("Pending command resolved.");
          speakText(textResponse);
          return;
        }
        pollTimer = setTimeout(() => pollStatus(pendingId), 900);
      } catch (error) {
        setStatus(error.message);
      }
    }

    async function sendCommand(overrideCommand = null) {
      const command = overrideCommand || commandInput.value.trim();
      if (!command) return;

      sendBtn.classList.add("sending");
      addBubble("user", command);
      commandInput.value = ""; 
      setStatus("Sending...");

      try {
        const response = await fetch("/command", {
          method: "POST",
          headers: authHeaders(),
          body: JSON.stringify({ command })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "Command failed.");

        if (data.status === "pending_confirmation" && data.pending_id) {
          addBubble("pending", `Pausing execution... awaiting grant in Widget Panel.`);
          showPending(data.pending_id);
          setStatus("Awaiting confirmation");
          pollStatus(data.pending_id);
          // Wait to speak if we need confirmation. Could also say "Please confirm this action..."
          speakText("Please confirm execution grant.");
          return;
        }

        const textResponse = formatResult(data.result);
        addBubble("assistant", textResponse);
        setStatus("Command completed.");
        speakText(textResponse);
      } catch (error) {
        addBubble("assistant", error.message);
        setStatus("Error: " + error.message);
      } finally {
        sendBtn.classList.remove("sending");
      }
    }

    async function resolvePending(action) {
      if (!activePendingId) return;
      
      setStatus(`Dispatching ${action}...`);
      try {
        const response = await fetch(`/${action}/${activePendingId}`, {
          method: "POST",
          headers: authHeaders()
        });
        if (!response.ok) throw new Error(`Action failed.`);
        pollStatus(activePendingId);
      } catch (error) {
        setStatus(error.message);
      }
    }

    async function uploadRecording(blob) {
      mainMicHub.classList.add("processing");
      setStatus("Transcribing audio...");

      const formData = new FormData();
      formData.append("audio", blob, "command.webm");

      try {
        const response = await fetch("/voice/transcribe", {
          method: "POST",
          headers: { "Authorization": authHeaders()["Authorization"] },
          body: formData
        });
        const data = await response.json();
        
        mainMicHub.classList.remove("processing");
        
        if (!response.ok) throw new Error("Transcription failed.");
        
        if (data.text) {
          // Immediately send the transcribed command
          sendCommand(data.text);
        } else {
          setStatus("No speech detected.");
        }
      } catch (err) {
        mainMicHub.classList.remove("processing");
        setStatus(err.message);
      }
    }

    async function startVoiceInput() {
      if (!navigator.mediaDevices) return setStatus("Microphone unavailable.");
      try {
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];
        mediaRecorder = new MediaRecorder(mediaStream);
      } catch (error) {
        return setStatus("Mic access denied.");
      }

      mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunks.push(e.data); };
      mediaRecorder.onstart = () => {
        isListening = true;
        mainMicHub.classList.add("recording");
        micIcon.innerHTML = `<rect x="6" y="6" width="12" height="12"></rect>`; // square stop icon
        // Cancel any audio playback when starting new voice input
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }
      };
      mediaRecorder.onstop = async () => {
        isListening = false;
        mainMicHub.classList.remove("recording");
        micIcon.innerHTML = `<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/>`;
        
        mediaStream.getTracks().forEach(t => t.stop());
        mediaStream = null;

        if (audioChunks.length) {
          const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
          await uploadRecording(blob);
          audioChunks = [];
        }
      };
      mediaRecorder.start();
    }

    mainVoiceBtn.addEventListener("click", () => {
      if (isListening) mediaRecorder.stop();
      else startVoiceInput();
    });

    sendBtn.addEventListener("click", () => sendCommand());
    clearBtn.addEventListener("click", () => {
      feedEl.innerHTML = "";
      hidePending();
    });
    approveBtn.addEventListener("click", () => resolvePending("confirm"));
    rejectBtn.addEventListener("click", () => resolvePending("reject"));
    
    commandInput.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey || !event.shiftKey) && event.key === "Enter") {
        event.preventDefault();
        sendCommand();
      }
    });

    // -----------------------------------------------------------------
    // CHAT HISTORY / SESSIONS DRAWER
    // -----------------------------------------------------------------
    const historyBtn = document.getElementById("historyBtn");
    const drawerOverlay = document.getElementById("drawerOverlay");
    const sessionsDrawer = document.getElementById("sessionsDrawer");
    const drawerCloseBtn = document.getElementById("drawerCloseBtn");
    const sessionsListView = document.getElementById("sessionsListView");
    const sessionsListContent = document.getElementById("sessionsListContent");
    const sessionMessagesView = document.getElementById("sessionMessagesView");
    const sessionMsgList = document.getElementById("sessionMsgList");
    const backToListBtn = document.getElementById("backToListBtn");

    function openDrawer() {
      drawerOverlay.classList.add("open");
      sessionsDrawer.classList.add("open");
      loadSessions();
    }

    function closeDrawer() {
      drawerOverlay.classList.remove("open");
      sessionsDrawer.classList.remove("open");
      // Reset to list view
      setTimeout(() => {
        sessionsListView.classList.remove("hidden");
        sessionMessagesView.classList.remove("visible");
      }, 350);
    }

    historyBtn.addEventListener("click", openDrawer);
    drawerOverlay.addEventListener("click", closeDrawer);
    drawerCloseBtn.addEventListener("click", closeDrawer);
    backToListBtn.addEventListener("click", () => {
      sessionsListView.classList.remove("hidden");
      sessionMessagesView.classList.remove("visible");
    });

    function formatDate(dateStr) {
      const d = new Date(dateStr + "T00:00:00");
      const today = new Date();
      const todayStr = today.toISOString().slice(0, 10);
      const yesterday = new Date(today);
      yesterday.setDate(today.getDate() - 1);
      const yesterdayStr = yesterday.toISOString().slice(0, 10);

      if (dateStr === todayStr) return "Today";
      if (dateStr === yesterdayStr) return "Yesterday";
      return d.toLocaleDateString(undefined, { weekday: 'long', month: 'short', day: 'numeric' });
    }

    async function loadSessions() {
      sessionsListContent.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted)">Loading sessions...</div>';
      try {
        const resp = await fetch("/chat/sessions?limit=30");
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Failed to load sessions");

        if (!data.sessions || data.sessions.length === 0) {
          sessionsListContent.innerHTML = `
            <div class="drawer-empty">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
              </svg>
              <p>No chat sessions yet.<br>Start a conversation to see history here.</p>
            </div>`;
          return;
        }

        sessionsListContent.innerHTML = data.sessions.map(s => {
          const isActive = s.status === 'active';
          const badge = isActive
            ? '<span class="session-badge badge-active">Active</span>'
            : '<span class="session-badge badge-closed">Closed</span>';
          const summary = s.summary
            ? `<div class="session-summary">${escapeHtml(s.summary)}</div>`
            : (isActive ? '<div class="no-summary">Session in progress...</div>' : '<div class="no-summary">No summary available</div>');

          return `
            <div class="session-card ${isActive ? 'active-session' : ''}" data-session-id="${s.id}">
              <div class="session-date">
                ${formatDate(s.session_date)}
                ${badge}
              </div>
              <div class="session-meta">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                ${s.message_count} messages
              </div>
              ${summary}
            </div>`;
        }).join('');

        // Attach click handlers for viewing session messages
        sessionsListContent.querySelectorAll('.session-card').forEach(card => {
          card.addEventListener('click', () => {
            const sessionId = card.dataset.sessionId;
            viewSessionMessages(sessionId);
          });
        });
      } catch (err) {
        sessionsListContent.innerHTML = `<div style="text-align:center; padding:40px; color:var(--danger)">${err.message}</div>`;
      }
    }

    async function viewSessionMessages(sessionId) {
      sessionsListView.classList.add("hidden");
      sessionMessagesView.classList.add("visible");
      sessionMsgList.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted)">Loading messages...</div>';

      try {
        const resp = await fetch(`/chat/history?limit=200`);
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Failed to load messages");

        if (!data.messages || data.messages.length === 0) {
          sessionMsgList.innerHTML = '<div style="text-align:center; padding:40px; color:var(--text-muted)">No messages in this session.</div>';
          return;
        }

        sessionMsgList.innerHTML = data.messages.map(m => {
          const isUser = m.role === 'user';
          const timeStr = m.created_at ? new Date(m.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'}) : '';
          const avatarClass = isUser ? '' : 'ai';
          const avatarSvg = isUser
            ? '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>'
            : '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>';

          return `
            <div>
              <div class="history-msg ${isUser ? 'history-user' : ''}">
                <div class="history-avatar ${avatarClass}">${avatarSvg}</div>
                <div class="history-bubble">${escapeHtml(m.content)}</div>
              </div>
              <div class="history-time" style="${isUser ? 'text-align:right' : ''}">${timeStr}</div>
            </div>`;
        }).join('');

        sessionMsgList.scrollTop = sessionMsgList.scrollHeight;
      } catch (err) {
        sessionMsgList.innerHTML = `<div style="text-align:center; padding:40px; color:var(--danger)">${err.message}</div>`;
      }
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    // -----------------------------------------------------------------
    // LOAD TODAY'S PERSISTED CHAT ON PAGE LOAD
    // -----------------------------------------------------------------
    async function loadTodayChat() {
      try {
        const resp = await fetch("/chat/history?limit=100");
        const data = await resp.json();
        if (!resp.ok || !data.messages || data.messages.length === 0) return;

        // Clear the default welcome message
        feedEl.innerHTML = '';

        data.messages.forEach(m => {
          if (m.role === 'user' || m.role === 'assistant') {
            const msgDiv = document.createElement('div');
            msgDiv.className = `message ${m.role}`;

            const timeStr = m.created_at
              ? new Date(m.created_at).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'})
              : '';

            let avatarHtml;
            if (m.role === 'assistant') {
              avatarHtml = '<div class="avatar ai"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2v20"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg></div>';
            } else {
              avatarHtml = '<div class="avatar"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg></div>';
            }

            msgDiv.innerHTML = `
              <div class="bubble-wrapper">
                ${avatarHtml}
                <div class="bubble ${m.role === 'user' ? 'user-bubble' : ''}">${escapeHtml(m.content)}</div>
              </div>
              <div class="msg-time">${timeStr}</div>`;

            feedEl.appendChild(msgDiv);
          }
        });

        feedEl.scrollTop = feedEl.scrollHeight;
      } catch (err) {
        console.error('Failed to load today chat:', err);
      }
    }

    // Load today's chat when page loads
    loadTodayChat();


    
  </script>
</body>
</html>
"""
