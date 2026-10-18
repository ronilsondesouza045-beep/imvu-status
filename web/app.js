"use strict";

/* ============================================================
   IMVU Status - versão web
   Tudo roda pelo backend (mesmo endereço): /api/nick, /api/sala,
   /checar e /gift. Login por email via Firebase (REST).
   ============================================================ */

const FB = {
  signup: "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=",
  login: "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=",
  lookup: "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=",
  oob: "https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode?key=",
  refresh: "https://securetoken.googleapis.com/v1/token?key=",
  idp: "https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key=",
};

const ERROS_FB = {
  EMAIL_EXISTS: "esse email já tem conta; toque em ENTRAR",
  EMAIL_NOT_FOUND: "email ou senha incorretos",
  INVALID_PASSWORD: "email ou senha incorretos",
  INVALID_LOGIN_CREDENTIALS: "email ou senha incorretos",
  INVALID_EMAIL: "email inválido",
  MISSING_PASSWORD: "digite a senha",
  MISSING_EMAIL: "digite o email",
  WEAK_PASSWORD: "senha muito curta (mínimo 6 caracteres)",
  PASSWORD_DOES_NOT_MEET_REQUIREMENTS: "senha muito fraca; use 6+ caracteres",
  USER_DISABLED: "essa conta foi desativada",
  TOO_MANY_ATTEMPTS_TRY_LATER: "muitas tentativas; espere alguns minutos",
  QUOTA_EXCEEDED: "limite do servidor de login; tente mais tarde",
  FEDERATED_USER_ID_ALREADY_LINKED: "esse email já tem conta; entre com o email e a senha",
  ACCOUNT_EXISTS_WITH_DIFFERENT_CREDENTIAL: "esse email já tem conta; entre com o email e a senha",
};

let CONFIG = {};
let CONTA = null;
let MONITOR = null;
let EMAIL_VISIVEL = false;
let UPDATE_URL_APK = null;
let MODO_CRIAR = false;
let OCUPADO_LOGIN = false;

const $ = (id) => document.getElementById(id);

function esc(texto) {
  return String(texto == null ? "" : texto)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function fbMsg(msg) {
  const chave = String(msg || "").split(" : ")[0].trim().toUpperCase();
  return ERROS_FB[chave] || "não deu para completar; tente de novo";
}

function ehWebView() {
  const ua = navigator.userAgent || "";
  return ua.includes("; wv") || ua.includes(" wv)") || ua.includes("Version/4.0");
}

function idAparelho() {
  try {
    let v = localStorage.getItem("imvu_aparelho");
    if (!v) {
      v = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)).replace(/-/g, "");
      localStorage.setItem("imvu_aparelho", v);
    }
    return v.slice(0, 32);
  } catch (e) {
    return "";
  }
}

function dataEpoch(seg) {
  const n = parseInt(seg, 10);
  if (!n) return "?";
  const d = new Date(n * 1000);
  return d.toISOString().slice(0, 10);
}

async function pedir(caminho, opcoes) {
  const resp = await fetch(caminho, opcoes || {});
  let dados = {};
  try { dados = await resp.json(); } catch (e) { dados = {}; }
  return { codigo: resp.status, dados: dados };
}

async function fbPost(url, corpo) {
  try {
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corpo),
    });
    const js = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const msg = (js && js.error && js.error.message) || ("HTTP " + resp.status);
      return { erro: msg };
    }
    return { js: js };
  } catch (e) {
    return { erro: "falha de rede: " + e.message };
  }
}

/* ---------------- conta local ---------------- */

function salvarContaLocal(dados) {
  CONTA = dados;
  try { localStorage.setItem("imvu_conta", JSON.stringify(dados)); } catch (e) {}
}

function limparContaLocal() {
  CONTA = null;
  try { localStorage.removeItem("imvu_conta"); } catch (e) {}
}

function carregarContaLocal() {
  try {
    const bruto = localStorage.getItem("imvu_conta");
    return bruto ? JSON.parse(bruto) : null;
  } catch (e) {
    return null;
  }
}

async function contaAtiva() {
  const guardada = carregarContaLocal();
  if (!guardada || !guardada.refreshToken || !CONFIG.firebase_key) return null;
  const r = await fbPost(FB.refresh + CONFIG.firebase_key, {
    grant_type: "refresh_token",
    refresh_token: guardada.refreshToken,
  });
  if (r.erro || !r.js || !r.js.id_token) {
    if (r.erro && /TOKEN_EXPIRED|INVALID_REFRESH_TOKEN|USER_NOT_FOUND|USER_DISABLED/i.test(r.erro)) {
      limparContaLocal();
    }
    return null;
  }
  const dados = {
    idToken: r.js.id_token,
    refreshToken: r.js.refresh_token || guardada.refreshToken,
    email: guardada.email || "",
    uid: r.js.user_id || guardada.uid || "",
    verificado: !!guardada.verificado,
  };
  const lk = await fbPost(FB.lookup + CONFIG.firebase_key, { idToken: dados.idToken });
  if (lk.js && lk.js.users && lk.js.users[0]) {
    dados.email = lk.js.users[0].email || dados.email;
    dados.verificado = !!lk.js.users[0].emailVerified;
  }
  salvarContaLocal(dados);
  return dados;
}

/* ---------------- telas ---------------- */

function avisoLogin(texto, tipo) {
  const el = $("login-aviso");
  el.textContent = texto || "";
  el.className = "aviso" + (tipo ? " " + tipo : "");
}

function entrarApp() {
  $("tela-login").classList.add("oculto");
  $("tela-app").classList.remove("oculto");
  atualizarConta();
}

function sairApp(mensagem) {
  limparContaLocal();
  pararMonitor();
  $("tela-app").classList.add("oculto");
  $("tela-login").classList.remove("oculto");
  if (mensagem) avisoLogin(mensagem, "aviso-cor");
}

function mostrarAba(nome) {
  document.querySelectorAll(".aba").forEach((s) => s.classList.add("oculto"));
  const secao = $("aba-" + nome);
  if (secao) secao.classList.remove("oculto");
  document.querySelectorAll(".nav button").forEach((b) => {
    b.classList.toggle("ativo", b.getAttribute("data-aba") === nome);
  });
}

/* ---------------- login ---------------- */

function mudarModo() {
  MODO_CRIAR = !MODO_CRIAR;
  $("btn-login").textContent = MODO_CRIAR ? "CRIAR CONTA" : "ENTRAR";
  $("btn-modo").textContent = MODO_CRIAR ? "já tenho conta? ENTRAR" : "não tem conta? CRIAR CONTA";
  $("login-senha2").classList.toggle("oculto", !MODO_CRIAR);
  $("login-senha2").autocomplete = MODO_CRIAR ? "new-password" : "off";
  avisoLogin("");
}

async function acaoPrincipal() {
  if (!CONFIG.firebase_key) {
    avisoLogin("O login ainda não foi ativado pelo dono do site.", "aviso-cor");
    return;
  }
  const email = ($("login-email").value || "").trim();
  const senha = $("login-senha").value || "";
  if (email.indexOf("@") < 1 || email.split("@").pop().indexOf(".") < 0) {
    avisoLogin("digite um email válido", "erro");
    return;
  }
  if (senha.length < 6) {
    avisoLogin("a senha precisa de pelo menos 6 caracteres", "erro");
    return;
  }
  if (MODO_CRIAR && senha !== $("login-senha2").value) {
    avisoLogin("as duas senhas precisam ser iguais", "erro");
    return;
  }
  if (OCUPADO_LOGIN) return;
  OCUPADO_LOGIN = true;
  $("login-spinner").classList.remove("oculto");
  avisoLogin(MODO_CRIAR ? "criando conta..." : "entrando...", "okmsg");

  if (MODO_CRIAR) {
    const r = await fbPost(FB.signup + CONFIG.firebase_key, {
      email: email, password: senha, returnSecureToken: true,
    });
    OCUPADO_LOGIN = false;
    $("login-spinner").classList.add("oculto");
    if (r.erro) {
      avisoLogin(fbMsg(r.erro), "erro");
      return;
    }
    await fbPost(FB.oob + CONFIG.firebase_key, {
      requestType: "VERIFY_EMAIL", idToken: r.js.idToken,
    });
    if (MODO_CRIAR) mudarModo();
    avisoLogin("conta criada! Enviamos um link de confirmação para seu email. Confirme e toque em ENTRAR.", "okmsg");
    return;
  }

  const r = await fbPost(FB.login + CONFIG.firebase_key, {
    email: email, password: senha, returnSecureToken: true,
  });
  OCUPADO_LOGIN = false;
  $("login-spinner").classList.add("oculto");
  if (r.erro) {
    avisoLogin(fbMsg(r.erro), "erro");
    return;
  }
  const dados = {
    idToken: r.js.idToken || "",
    refreshToken: r.js.refreshToken || "",
    email: r.js.email || email,
    uid: r.js.localId || "",
    verificado: false,
  };
  const lk = await fbPost(FB.lookup + CONFIG.firebase_key, { idToken: dados.idToken });
  if (lk.js && lk.js.users && lk.js.users[0]) {
    dados.verificado = !!lk.js.users[0].emailVerified;
  }
  salvarContaLocal(dados);
  avisoLogin("");
  entrarApp();
}

async function esqueciSenha() {
  if (!CONFIG.firebase_key) {
    avisoLogin("O login ainda não foi ativado pelo dono do site.", "aviso-cor");
    return;
  }
  const email = ($("login-email").value || "").trim();
  if (email.indexOf("@") < 1) {
    avisoLogin("digite seu email no campo de cima primeiro", "aviso-cor");
    return;
  }
  if (OCUPADO_LOGIN) return;
  OCUPADO_LOGIN = true;
  $("login-spinner").classList.remove("oculto");
  const r = await fbPost(FB.oob + CONFIG.firebase_key, {
    requestType: "PASSWORD_RESET", email: email,
  });
  OCUPADO_LOGIN = false;
  $("login-spinner").classList.add("oculto");
  if (r.erro) {
    avisoLogin(fbMsg(r.erro), "erro");
  } else {
    avisoLogin("se esse email tiver conta, mandamos um link para trocar a senha. Olhe a caixa de entrada (e o spam).", "okmsg");
  }
}

/* ---------------- Google (opcional) ---------------- */

async function onGoogleCredential(resp) {
  if (!resp || !resp.credential) return;
  avisoLogin("entrando com o Google...", "okmsg");
  const r = await fbPost(FB.idp + CONFIG.firebase_key, {
    postBody: "id_token=" + resp.credential + "&providerId=google.com",
    requestUri: location.origin,
    returnIdpCredential: true,
    returnSecureToken: true,
  });
  if (r.erro || !r.js || !r.js.idToken) {
    avisoLogin(fbMsg(r.erro || "login google falhou"), "erro");
    return;
  }
  salvarContaLocal({
    idToken: r.js.idToken,
    refreshToken: r.js.refreshToken || "",
    email: r.js.email || "",
    uid: r.js.localId || "",
    verificado: true,
  });
  avisoLogin("");
  entrarApp();
}

function ligarGoogle() {
  if (!CONFIG.google_client_id || !CONFIG.firebase_key) return;
  const s = document.createElement("script");
  s.src = "https://accounts.google.com/gsi/client";
  s.async = true;
  s.onload = () => {
    try {
      google.accounts.id.initialize({
        client_id: CONFIG.google_client_id,
        callback: onGoogleCredential,
      });
      google.accounts.id.renderButton($("google-btn"), {
        theme: "filled_black", size: "large", shape: "pill",
        text: "continue_with", locale: "pt-BR",
      });
      $("google-box").classList.remove("oculto");
    } catch (e) {}
  };
  document.head.appendChild(s);
}

/* ---------------- aba NICK ---------------- */

function rotuloDisponibilidade(av) {
  const rotulos = {
    available: "Disponível", online: "Online", away: "Ausente",
    dnd: "Não perturbe (DND)", offline: "Offline",
  };
  return rotulos[av] || av;
}

function montarCardNick(d) {
  const linhas = [];
  linhas.push("<b>" + esc(d.display_name || d.nick) + "</b>  (" + esc(d.nick) + " | CID " + esc(d.cid) + ")");
  if (d.online) {
    linhas.push('Status: <span class="ok">ONLINE</span>');
  } else {
    linhas.push('Status: <span class="off">OFFLINE</span>');
  }
  if (d.availability) {
    linhas.push("Disponibilidade: " + esc(rotuloDisponibilidade(d.availability)));
  }
  if (d.sala) {
    const priv = String(d.sala.privacidade || "").indexOf("priv") >= 0;
    const pub = String(d.sala.privacidade || "").indexOf("pub") >= 0;
    const tipo = priv ? '<span class="privada">PRIVADA</span>' : (pub ? "pública" : esc(d.sala.privacidade));
    linhas.push('Sala atual: "' + esc(d.sala.nome) + '" (' + tipo + ")");
    if (!d.online) {
      linhas.push('<span class="italico">(status offline, mas o perfil aponta essa sala — pode ser modo "aparecer offline")</span>');
    }
  } else if (d.online) {
    linhas.push("Sala atual: não exposta agora");
  }
  linhas.push("País: " + esc(d.pais || "?") + " | Membro desde " + esc(dataEpoch(d.registrado)));
  if (d.seguidores != null) linhas.push("Seguidores: ~" + esc(d.seguidores));
  if (d.look) {
    linhas.push("Avatar/look: " + esc(d.look.corpo || "look atual") + " (" + esc(d.look.total) + " itens)");
  }
  if (d.marcas && d.marcas.length) linhas.push("Perfil: " + esc(d.marcas.join(", ")));
  if (d.tagline) linhas.push('<span class="italico">' + esc(d.tagline) + "</span>");
  return linhas.join("\n");
}

function mostrarFoto(url) {
  const img = $("foto-avatar");
  if (url) {
    img.onload = () => img.classList.remove("oculto");
    img.onerror = () => img.classList.add("oculto");
    img.src = url;
  } else {
    img.classList.add("oculto");
    img.removeAttribute("src");
  }
}

async function checarNick(automatico) {
  const nick = ($("nick-field").value || "").trim();
  const resultado = $("resultado-nick");
  if (!nick) {
    if (!automatico) resultado.textContent = "Digite um nick primeiro.";
    return;
  }
  $("spinner-nick").classList.remove("oculto");
  if (!automatico) {
    resultado.textContent = "Consultando...";
    mostrarFoto(null);
  }
  let r;
  try {
    r = await pedir("/api/nick?" + new URLSearchParams({ nick: nick }));
  } catch (e) {
    r = { codigo: 0, dados: { erro: "falha de rede: " + e.message } };
  }
  $("spinner-nick").classList.add("oculto");
  if (r.dados && r.dados.erro) {
    resultado.textContent = r.dados.erro;
    if (r.codigo === 404) mostrarFoto(null);
    return;
  }
  mostrarFoto(r.dados.foto_perfil || null);
  resultado.innerHTML = montarCardNick(r.dados);
}

function alternarMonitor() {
  if (MONITOR) {
    pararMonitor();
    return;
  }
  const nick = ($("nick-field").value || "").trim();
  if (!nick) {
    $("resultado-nick").textContent = "Digite um nick para monitorar.";
    return;
  }
  let intervalo = parseFloat(String($("intervalo-field").value).replace(",", "."));
  if (isNaN(intervalo)) intervalo = 30;
  intervalo = Math.min(600, Math.max(15, intervalo));
  $("intervalo-field").value = intervalo;
  $("btn-monitor").textContent = "PARAR";
  checarNick(false);
  MONITOR = setInterval(() => checarNick(true), intervalo * 1000);
}

function pararMonitor() {
  if (MONITOR) {
    clearInterval(MONITOR);
    MONITOR = null;
  }
  const btn = $("btn-monitor");
  if (btn) btn.textContent = "MONITORAR";
}

/* ---------------- aba SALA ---------------- */

async function buscarSala() {
  const rid = ($("sala-field").value || "").trim();
  const resultado = $("resultado-sala");
  if (!rid) {
    resultado.textContent = "Cole o ID da sala (ex: 12345678-1).";
    return;
  }
  resultado.textContent = "Buscando...";
  let r;
  try {
    r = await pedir("/api/sala?" + new URLSearchParams({ id: rid }));
  } catch (e) {
    r = { codigo: 0, dados: { erro: "falha de rede: " + e.message } };
  }
  const d = r.dados || {};
  if (d.erro) {
    resultado.textContent = d.erro;
    return;
  }
  const linhas = [];
  linhas.push("<b>" + esc(d.nome) + "</b>  (" + esc(d.id) + ")");
  linhas.push("Dono: " + esc(d.owner));
  linhas.push("Ocupação: " + esc(d.count) + "/" + esc(d.max));
  if (d.desc) linhas.push('<span class="italico">' + esc(d.desc) + "</span>");
  if (d.participantes && d.participantes.length) {
    linhas.push("<b>Participantes:</b>");
    d.participantes.forEach((p) => {
      linhas.push("  • " + esc(p[0]) + (p[1] ? " (" + esc(p[1]) + " KB)" : ""));
    });
  } else if (!(String(d.count) === "0" || d.count === "?" || d.count == null)) {
    linhas.push("(lista de participantes não exposta)");
  } else {
    linhas.push("sala vazia");
  }
  resultado.innerHTML = linhas.join("\n");
}

/* ---------------- aba CONTA ---------------- */

function emailMascarado(email) {
  email = String(email || "");
  if (email.indexOf("@") < 0) return email;
  const partes = email.split("@");
  const usuario = partes[0];
  const dominio = partes[1];
  if (usuario.length <= 2) return usuario.slice(0, 1) + "***@" + dominio;
  return usuario.slice(0, 1) + "***" + usuario.slice(-1) + "@" + dominio;
}

function atualizarConta() {
  const dados = CONTA || carregarContaLocal() || {};
  const email = dados.email || "";
  const statusEl = $("conta-status");
  statusEl.style.color = "";
  if (email) {
    if (dados.verificado) {
      statusEl.innerHTML = '<span class="ok">conta confirmada</span>';
      $("btn-reenviar").classList.add("oculto");
    } else {
      statusEl.innerHTML = '<span class="alerta">falta confirmar o email — veja a caixa de entrada e o spam</span>';
      $("btn-reenviar").classList.remove("oculto");
    }
  } else {
    statusEl.innerHTML = '<span class="alerta">sem conta — o resgate fica bloqueado</span>';
  }
  $("conta-email").textContent = email ? (EMAIL_VISIVEL ? email : emailMascarado(email)) : "sem email cadastrado";
}

async function alternarEmail() {
  const dados = CONTA || carregarContaLocal() || {};
  if (!dados.email) return;
  EMAIL_VISIVEL = !EMAIL_VISIVEL;
  atualizarConta();
}

async function reenviarVerificacao() {
  const dados = await contaAtiva();
  if (!dados) {
    avisoConta("sua sessão expirou; saia e entre de novo", "erro");
    return;
  }
  const r = await fbPost(FB.oob + CONFIG.firebase_key, {
    requestType: "VERIFY_EMAIL", idToken: dados.idToken,
  });
  if (r.erro) {
    avisoConta(fbMsg(r.erro), "erro");
  } else {
    avisoConta("email de confirmação reenviado! Confira a caixa de entrada e o spam.", "okmsg");
  }
}

function avisoConta(texto, tipo) {
  const el = $("conta-status");
  el.textContent = texto;
  el.className = "fraco";
  if (tipo === "erro") el.style.color = "var(--vermelho)";
  else if (tipo === "okmsg") el.style.color = "var(--verde)";
  else el.style.color = "";
}

/* ---------------- aba PRESENTE ---------------- */

function spinnerPresente(ativo) {
  $("spinner-presente").classList.toggle("oculto", !ativo);
}

async function contarToken(obrigatorio) {
  if (!CONFIG.firebase_key) return "";
  const dados = await contaAtiva();
  if (!dados) {
    if (obrigatorio) {
      $("resultado-presente").textContent = "entre na sua conta (aba CONTA) para resgatar o presente";
    }
    return null;
  }
  return dados.idToken;
}

async function verificarPresente() {
  const nick = ($("presente-nick").value || "").trim();
  const resultado = $("resultado-presente");
  if (!nick) {
    resultado.textContent = "Digite o nick primeiro.";
    return;
  }
  spinnerPresente(true);
  resultado.textContent = "Consultando nick e wishlist...";
  const token = await contarToken(true);
  if (token === null) {
    spinnerPresente(false);
    return;
  }
  const cabecalho = {};
  if (token) cabecalho["X-Conta-Token"] = token;
  let r;
  try {
    r = await pedir("/checar?" + new URLSearchParams({ nick: nick }), { headers: cabecalho });
  } catch (e) {
    r = { codigo: 0, dados: { erro: "falha de rede: " + e.message } };
  }
  spinnerPresente(false);
  const d = r.dados || {};
  if (d.erro) {
    resultado.textContent = d.erro;
    return;
  }
  const linhas = [];
  linhas.push("<b>" + esc(d.nick) + "</b>  (user-" + esc(d.cid) + ")");
  if (d.ja_recebeu) {
    const quando = String(d.quando || "").slice(0, 16).replace("T", " ");
    const volta = d.volta_em ? " Pode receber de novo " + esc(d.volta_em) + "." : " Pode receber de novo depois de 24h.";
    linhas.push('<span class="alerta">Esse avatar já recebeu os ' + esc(d.creditos || 100) + " créditos" + (quando ? " (" + esc(quando) + ")" : "") + "." + volta + "</span>");
  } else if (d.apto === false) {
    linhas.push('<span class="off">Não pode receber agora: ' + esc(d.motivo || "conta não elegível") + ".</span>");
  } else {
    linhas.push('<span class="ok">Pode resgatar ' + esc(d.creditos || 100) + " créditos agora!</span>");
  }
  const wl = d.wishlist || {};
  if (wl.disponivel) {
    const itens = (wl.itens || []).map((item) => {
      const preco = item.preco ? " (" + esc(item.preco) + " cr)" : "";
      return esc(item.nome || "item") + preco;
    });
    linhas.push("Wishlist (" + esc(wl.total != null ? wl.total : itens.length) + " itens): " + (itens.length ? itens.join(", ") : "vazia"));
  } else {
    linhas.push('<span class="italico">Wishlist: indisponível agora' + (wl.motivo ? " (" + esc(wl.motivo) + ")" : "") + ".</span>");
  }
  if (d.restantes_hoje != null) {
    linhas.push('<span class="italico">Envios restantes hoje: ' + esc(d.restantes_hoje) + "</span>");
  }
  resultado.innerHTML = linhas.join("\n");
}

async function pegarPresente() {
  const nick = ($("presente-nick").value || "").trim();
  const resultado = $("resultado-presente");
  if (!nick) {
    resultado.textContent = "Digite seu nick primeiro.";
    return;
  }
  if (!$("presente-confirmo").checked) {
    resultado.textContent = "Marque a caixinha confirmando que o nick é a sua conta no IMVU antes de resgatar.";
    return;
  }
  const token = await contarToken(true);
  if (token === null) return;
  spinnerPresente(true);
  resultado.textContent = "Enviando créditos...";
  let r;
  try {
    r = await pedir("/gift", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nick: nick,
        mensagem: ($("presente-msg").value || "").trim(),
        aparelho: idAparelho(),
        token: token || "",
      }),
    });
  } catch (e) {
    r = { codigo: 0, dados: { erro: "falha de rede: " + e.message } };
  }
  spinnerPresente(false);
  const d = r.dados || {};
  if (d.ok) {
    resultado.innerHTML = '<span class="ok"><b>' + esc(d.creditos || 100) + ' créditos enviados!</b></span>\nConfira sua carteira (Wallet) no IMVU. Aproveite!';
  } else if (d.erro) {
    resultado.textContent = d.erro;
  } else {
    resultado.textContent = "Não deu agora. Tente de novo mais tarde.";
  }
}

/* ---------------- aba APP ---------------- */

function prepararAbaApp() {
  $("app-versao").textContent = "Versão do site: v" + (CONFIG.versao_site || "?");
  if (ehWebView()) {
    $("app-aviso-webview").classList.remove("oculto");
    $("app-aviso-browser").classList.add("oculto");
  } else {
    $("app-aviso-webview").classList.add("oculto");
    $("app-aviso-browser").classList.remove("oculto");
  }
}

function urlReleases() {
  return "https://github.com/" + (CONFIG.repo_github || "ronilsondesouza045-beep/imvu-status") + "/releases/latest";
}

async function baixarApk() {
  if (UPDATE_URL_APK) {
    window.open(UPDATE_URL_APK, "_blank");
    return;
  }
  window.open(urlReleases(), "_blank");
}

async function verificarUpdate() {
  const titulo = $("update-titulo");
  const notas = $("update-notas");
  titulo.textContent = "Verificando...";
  notas.textContent = "";
  const repo = CONFIG.repo_github || "ronilsondesouza045-beep/imvu-status";
  let js = null;
  try {
    const resp = await fetch("https://api.github.com/repos/" + repo + "/releases/latest", {
      headers: { Accept: "application/vnd.github+json" },
    });
    if (resp.ok) js = await resp.json();
  } catch (e) {}
  if (!js) {
    titulo.textContent = "Não foi possível verificar agora. Tente de novo.";
    return;
  }
  const tag = String(js.tag_name || "");
  UPDATE_URL_APK = null;
  (js.assets || []).forEach((a) => {
    const u = String(a.browser_download_url || "");
    if (u.toLowerCase().endsWith(".apk")) UPDATE_URL_APK = u;
  });
  if (ehWebView()) {
    titulo.innerHTML = '<span class="ok">Você está sempre na versão mais recente (v' + esc(CONFIG.versao_site) + ")</span> — o app abre o site e recebe as novidades sozinho.";
  } else {
    titulo.innerHTML = '<span class="ok">Site: versão v' + esc(CONFIG.versao_site) + "</span>  |  App Android: " + (tag ? esc(tag) : "(sem release ainda)");
  }
  notas.textContent = String(js.body || "").trim() || "Sem notas nesta versão.";
}

/* ---------------- inicialização ---------------- */

async function carregarConfig() {
  const r = await pedir("/api/config");
  if (!r.dados || !r.dados.ok) throw new Error("config");
  CONFIG = r.dados;
}

async function iniciar() {
  document.querySelectorAll(".nav button").forEach((b) => {
    b.addEventListener("click", () => mostrarAba(b.getAttribute("data-aba")));
  });
  $("btn-login").addEventListener("click", acaoPrincipal);
  $("btn-modo").addEventListener("click", mudarModo);
  $("btn-esqueci").addEventListener("click", esqueciSenha);
  $("login-senha").addEventListener("keydown", (e) => { if (e.key === "Enter") acaoPrincipal(); });

  $("btn-checar").addEventListener("click", () => checarNick(false));
  $("btn-monitor").addEventListener("click", alternarMonitor);
  $("nick-field").addEventListener("keydown", (e) => { if (e.key === "Enter") checarNick(false); });

  $("btn-sala").addEventListener("click", buscarSala);
  $("sala-field").addEventListener("keydown", (e) => { if (e.key === "Enter") buscarSala(); });

  $("btn-ver-email").addEventListener("click", alternarEmail);
  $("btn-reenviar").addEventListener("click", reenviarVerificacao);
  $("btn-sair").addEventListener("click", () => sairApp("você saiu da conta"));

  $("btn-resgatar").addEventListener("click", pegarPresente);
  $("btn-wishlist").addEventListener("click", verificarPresente);

  $("btn-update").addEventListener("click", verificarUpdate);
  $("btn-baixar-apk").addEventListener("click", baixarApk);
  $("btn-recarregar").addEventListener("click", () => location.reload());

  avisoLogin("conectando ao servidor... (a primeira visita pode demorar um pouco)");
  try {
    await carregarConfig();
  } catch (e) {
    avisoLogin("não consegui falar com o servidor. Recarregue a página e tente de novo.", "erro");
    return;
  }

  if (!CONFIG.firebase_key) {
    // login desligado pelo dono: entra direto (modo antigo do aparelho)
    avisoLogin("");
    entrarApp();
    return;
  }

  ligarGoogle();

  const dados = await contaAtiva();
  if (dados) {
    avisoLogin("");
    entrarApp();
  } else if (carregarContaLocal() && carregarContaLocal().refreshToken) {
    avisoLogin("não deu para renovar o login agora; entre de novo.", "aviso-cor");
  } else {
    avisoLogin("");
  }
}

iniciar();
