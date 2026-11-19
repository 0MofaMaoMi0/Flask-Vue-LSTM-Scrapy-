<template><main class="screen welcome active" id="welcome" aria-label="欢迎页">
    <div class="welcome-content">
      <svg class="logo" viewBox="0 0 110 110" fill="none" aria-hidden="true"><circle cx="52" cy="53" r="38" stroke="#e4d6c3" stroke-width="1.8"/><circle cx="52" cy="53" r="12" stroke="#e4d6c3" stroke-width="1.6"/><circle cx="52" cy="25" r="9.5" fill="#e5d9c8"/><circle cx="79" cy="44" r="9.5" fill="#e5d9c8"/><circle cx="68" cy="77" r="9.5" fill="#e5d9c8"/><circle cx="36" cy="77" r="9.5" fill="#e5d9c8"/><circle cx="25" cy="44" r="9.5" fill="#e5d9c8"/><path d="M84 83l20 15" stroke="#e5d9c8" stroke-width="3"/></svg>
      <p class="eyebrow">MovieInsight · 电影发现</p>
      <div class="rule"></div>
      <h1 class="static-welcome">Welcome back.</h1>
      <p class="subtitle">登录以使用完整功能</p>
      <button class="welcome-login" type="button" @click="$router.push('/login')">登 录</button>
      <button class="register-link" type="button" @click="$router.push('/register')">注 册</button>
      <div class="quiet-line" aria-hidden="true"></div>
    </div>
  </main></template>
<style scoped>
.welcome-content{width:min(100%,420px)}
.eyebrow{color:#b7b1a5;letter-spacing:.04em;font-size:11px;font-weight:500;margin:0 0 24px 1px}
.rule{width:32px;height:1px;background:#c8a46a;margin-bottom:28px}
.welcome h1.static-welcome{font-family:var(--serif);font-size:clamp(40px,5vw,64px);font-weight:500;letter-spacing:.01em;line-height:1.16;margin:0 0 18px;color:#fff;text-shadow:none}
.subtitle{font-size:13px;color:#b7b1a5;letter-spacing:.02em;line-height:1.8;margin:0 0 44px}
button.welcome-login{width:min(320px,100%);height:48px;border-radius:8px;background:#e9dfcd;border:1px solid #e9dfcd;color:#171410;font-size:13px;font-weight:500;letter-spacing:.14em;box-shadow:none;transition:background-color .18s ease,border-color .18s ease,transform .18s ease}
button.welcome-login:hover,button.welcome-login:focus-visible{background:#f2e9d9;border-color:#f2e9d9;transform:translateY(-1px);box-shadow:none;color:#171410}
button.welcome-login:active{transform:translateY(0)}
.register-link{background:none;border:0;color:#b7b1a5;font-size:12px;letter-spacing:.14em;margin-top:16px;padding:10px 24px;border-radius:6px;transition:color .18s ease}
.register-link:hover,.register-link:focus-visible{color:#f1e9da}
.quiet-line{width:96px;height:1px;background:rgba(241,236,226,.18);margin-top:8px}
@media(max-width:720px){.welcome-content{width:100%}.welcome h1.static-welcome{font-size:clamp(32px,9vw,44px)}.subtitle{margin-bottom:32px}button.welcome-login{height:46px}}
</style>