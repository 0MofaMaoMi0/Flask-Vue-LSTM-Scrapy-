<template><main class="screen welcome active" id="welcome" aria-label="欢迎页">
    <div class="welcome-content">
      <svg class="logo" viewBox="0 0 110 110" fill="none" aria-hidden="true"><circle cx="52" cy="53" r="38" stroke="#e4d6c3" stroke-width="1.8"/><circle cx="52" cy="53" r="12" stroke="#e4d6c3" stroke-width="1.6"/><circle cx="52" cy="25" r="9.5" fill="#e5d9c8"/><circle cx="79" cy="44" r="9.5" fill="#e5d9c8"/><circle cx="68" cy="77" r="9.5" fill="#e5d9c8"/><circle cx="36" cy="77" r="9.5" fill="#e5d9c8"/><circle cx="25" cy="44" r="9.5" fill="#e5d9c8"/><path d="M84 83l20 15" stroke="#e5d9c8" stroke-width="3"/></svg>
      <p class="eyebrow">MOVIEINSIGHT · AN OPENING SCENE</p>
      <div class="rule"></div>
      <h1 class="static-welcome">Welcome back.</h1>
      <p class="subtitle">登录以使用完整功能</p>
      <button class="liquid welcome-login" type="button" @click="$router.push('/login')">登 录</button>
      <button class="register-link" type="button" @click="$router.push('/register')">注 册</button>
      <div class="quiet-line" aria-hidden="true"></div>
    </div>
  </main></template>