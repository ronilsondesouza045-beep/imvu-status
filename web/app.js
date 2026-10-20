'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const APP_MODE = new URLSearchParams(location.search).get('app') === '1';
const LINK_ALVO = APP_MODE ? '' : ' target="_blank" rel="noopener"';

const state = {
  device: '',
  nick: null,
  avatar: null,
  history: null,
  completo: null,
  catalog: { q: '', tipo: 'produtos', ordenar: 'relevancia', pagina: 1 },
  watch: [],
  alerts: [],
  hooks: [],
  charts: {},
  ws: null,
  reconnect: null,
};

const TITULOS = {
  painel: ['Painel', 'Visao geral do movimento em tempo real.'],
  avatar: ['Avatar', 'Perfil publico, outfit, historico e previsao de um nick.'],
  salas: ['Salas', 'Ranking, ocupacao ao vivo e diretorio publico.'],
  catalogo: ['Catalogo', 'Busque produtos e criadores da loja do IMVU.'],
  alertas: ['Alertas', 'Acompanhe avatares e receba avisos.'],
  api: ['API', 'Endpoints publicos para integrar.'],
};

const TIPOS = {
  online: 'Entrou online',
  offline: 'Ficou offline',
  sala: 'Mudou de sala',
  look: 'Trocou de look',
  disponibilidade: 'Mudou a disponibilidade',
  suspeita: 'Possivel modo offline',
};

const esc = (valor) =>
  String(valor ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[c]);

function novoDevice() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

function deviceId() {
  let id = localStorage.getItem('imvu.device');
  if (!id || !/^[a-f0-9]{32}$/.test(id)) {
    id = novoDevice();
    localStorage.setItem('imvu.device', id);
  }
  return id;
}

async function api(caminho, opcoes = {}) {
  const config = {
    method: opcoes.method || 'GET',
    headers: { Accept: 'application/json' },
  };
  if (opcoes.body) {
    config.headers['Content-Type'] = 'application/json';
    config.body = opcoes.body;
  }
  const resposta = await fetch(caminho, config);
  let dados = null;
  try { dados = await resposta.json(); } catch (_) { dados = null; }
  if (!resposta.ok) {
    const detalhe = dados && (dados.detail || dados.erro || dados.mensagem);
    throw new Error(detalhe || ('HTTP ' + resposta.status));
  }
  return dados;
}

function fmtData(iso) {
  if (!iso) return '--';
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return String(iso);
  return data.toLocaleString('pt-BR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  });
}

function fmtHora(iso) {
  if (!iso) return '--';
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return String(iso);
  return data.toLocaleString('pt-BR', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

function fmtMinutos(min) {
  const valor = Number(min) || 0;
  if (valor < 60) return valor + ' min';
  const horas = Math.floor(valor / 60);
  const resto = valor % 60;
  return horas + 'h' + (resto ? ' ' + resto + 'min' : '');
}

function toast(mensagem, erro = false) {
  const el = $('#toast');
  el.textContent = mensagem;
  el.className = 'toast show' + (erro ? ' err' : '');
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = 'toast'; }, 3800);
}

function setLive(ativo) {
  const dot = $('#live-dot');
  const label = $('#live-label');
  if (!dot || !label) return;
  dot.classList.toggle('on', ativo);
  label.textContent = ativo ? 'ao vivo' : 'reconectando';
}

function irPara(pagina) {
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.page === pagina));
  $$('.page').forEach((p) => p.classList.toggle('active', p.id === 'page-' + pagina));
  const titulo = TITULOS[pagina] || ['', ''];
  $('#page-title').textContent = titulo[0];
  $('#page-sub').textContent = titulo[1];
  if (pagina === 'painel') carregarPainel();
  if (pagina === 'salas') carregarSalas();
  if (pagina === 'catalogo' && $('#catalog-q')) $('#catalog-q').focus();
  if (pagina === 'alertas') { carregarWatch(); carregarAlertas(); carregarHooks(); }
}

function configurarNavegacao() {
  $$('#nav button').forEach((botao) => {
    botao.addEventListener('click', () => irPara(botao.dataset.page));
  });
  const params = new URLSearchParams(location.search);
  const pagina = params.get('pagina');
  if (pagina && TITULOS[pagina]) irPara(pagina);
}

function relogio() {
  const agora = new Date();
  $('#clock').textContent = agora.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
}

async function carregarConfig() {
  try {
    const cfg = await api('/api/config');
    $('#api-version').textContent = 'v' + cfg.versao;
    document.title = 'IMVU Status v' + cfg.versao;
  } catch (_) { /* silencioso */ }
}

function metricas(itens) {
  return itens.map((item) => (
    '<div class="metric"><small>' + esc(item.rotulo) + '</small><strong>' +
    esc(item.valor) + '</strong>' + (item.extra ? '<span>' + esc(item.extra) + '</span>' : '') +
    '</div>'
  )).join('');
}

async function carregarPainel() {
  const alvoDigest = $('#digest-lines');
  const alvoHealth = $('#health-grid');
  const alvoRooms = $('#painel-rooms');
  const alvoHeat = $('#heatmap');

  api('/api/digest').then((dados) => {
    alvoDigest.innerHTML = dados.linhas.map((linha) => '<p>' + esc(linha) + '</p>').join('');
    const ia = $('#digest-ai');
    if (dados.texto_ia) {
      ia.style.display = 'block';
      ia.textContent = dados.texto_ia;
    } else {
      ia.style.display = 'none';
    }
  }).catch(() => {
    alvoDigest.innerHTML = '<p class="empty">Nao foi possivel carregar o resumo.</p>';
  });

  api('/api/health').then((saude) => {
    const coletor = saude.coletor || {};
    alvoHealth.innerHTML = metricas([
      { rotulo: 'Banco', valor: saude.db ? 'ok' : 'falha' },
      { rotulo: 'Coletor', valor: coletor.desligado ? 'off' : (coletor.ciclos || 0) + ' ciclos' },
      { rotulo: 'Consultas', valor: coletor.consultas || 0, extra: 'desde o inicio' },
      { rotulo: 'Conexoes', valor: saude.conexoes || 0, extra: 'painel em tempo real' },
      { rotulo: 'Versao', valor: 'v' + saude.versao },
    ]);
  }).catch(() => {
    alvoHealth.innerHTML = '<p class="empty">Servidor indisponivel.</p>';
  });

  api('/api/rooms/top?horas=24&limite=8').then((dados) => {
    alvoRooms.innerHTML = htmlSalas(dados.salas, 'Sem salas monitoradas ainda.');
  }).catch(() => { alvoRooms.innerHTML = '<p class="empty">Sem dados de salas.</p>'; });

  api('/api/rooms/heatmap?horas=168').then((dados) => {
    desenharHeatmap(alvoHeat, dados);
  }).catch(() => { alvoHeat.innerHTML = '<p class="empty">Sem dados de calor.</p>'; });
}

$('#digest-refresh').addEventListener('click', carregarPainel);

function htmlSalas(salas, vazio) {
  if (!salas || !salas.length) return '<p class="empty">' + esc(vazio) + '</p>';
  return salas.map((sala) => {
    const tendencia = Number(sala.tendencia) || 0;
    const classe = tendencia > 0 ? 'trend-up' : (tendencia < 0 ? 'trend-down' : 'room-meta');
    const sinal = tendencia > 0 ? '+' + tendencia : String(tendencia);
    return (
      '<div class="room-row" data-rid="' + esc(sala.rid) + '">' +
      '<div><div class="room-name">' + esc(sala.nome || 'Sala sem nome') + '</div>' +
      '<div class="room-meta">' + esc(sala.dono || 'dono desconhecido') + ' - media ' + esc(sala.media) +
      ' - pico ' + esc(sala.pico) + '</div></div>' +
      '<div class="room-count">' + esc(sala.online) + '</div>' +
      '<div class="' + classe + '">' + esc(sinal) + '</div>' +
      '</div>'
    );
  }).join('');
}

function desenharHeatmap(alvo, dados) {
  if (!alvo) return;
  if (!dados || !dados.matriz || !dados.matriz.length) {
    alvo.innerHTML = '<p class="empty">Sem dados suficientes.</p>';
    return;
  }
  const maximo = Math.max(1, ...dados.matriz.flat());
  let html = '<div class="heatmap-grid"><div></div>';
  for (let hora = 0; hora < 24; hora += 1) {
    html += '<div class="heat-label">' + (hora % 3 === 0 ? hora + 'h' : '') + '</div>';
  }
  dados.matriz.forEach((linha, dia) => {
    html += '<div class="heat-day">' + esc(dados.dias[dia]) + '</div>';
    linha.forEach((valor, hora) => {
      const intensidade = Math.min(0.92, 0.06 + (Number(valor) / maximo) * 0.86);
      html += '<div class="heat-cell" title="' + esc(dados.dias[dia] + ' ' + hora + 'h: ' + valor) +
        ' pessoas" style="background:rgba(220,38,38,' + intensidade.toFixed(2) + ')"></div>';
    });
  });
  html += '</div><div class="heat-legend"><span>menos</span><span class="heat-scale"></span><span>mais</span></div>';
  alvo.innerHTML = html;
}

function configurarAvatar() {
  $('#buscar-btn').addEventListener('click', () => buscarAvatar($('#nick-input').value));
  $('#nick-input').addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') buscarAvatar($('#nick-input').value);
  });
  $('#watch-btn').addEventListener('click', alternarWatch);
}

async function buscarAvatar(nick) {
  const alvo = String(nick || '').trim();
  if (!alvo) { toast('Digite um nick.', true); return; }
  const botao = $('#buscar-btn');
  botao.disabled = true;
  botao.textContent = 'Consultando...';
  try {
    const perfil = await api('/api/status/' + encodeURIComponent(alvo));
    state.nick = perfil.nick;
    state.avatar = perfil;
    state.history = null;
    $('#nick-input').value = perfil.nick;
    $('#watch-btn').disabled = false;
    renderPerfil(perfil);
    renderPrevisao(perfil.previsao);
    atualizarBotaoWatch();
    const [historico, looks, completo] = await Promise.allSettled([
      api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/history?horas=168'),
      api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/looks'),
      api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/profile?produtos=30'),
    ]);
    if (historico.status === 'fulfilled') {
      state.history = historico.value;
      renderHistorico(historico.value);
    } else {
      renderHistoricoVazio('Historico ainda sendo formado. Volte em alguns minutos.');
    }
    if (looks.status === 'fulfilled') {
      renderLooks(looks.value.looks || []);
    } else {
      $('#avatar-looks').innerHTML = '<p class="empty">Sem looks registrados.</p>';
    }
    if (completo.status === 'fulfilled') {
      state.completo = completo.value;
      renderOutfit(completo.value);
      renderDetalhes(completo.value);
      renderPerfilCompleto(completo.value);
      const itensOutfit = ((completo.value.outfit || {}).itens || []).length;
      if (!itensOutfit) {
        setTimeout(() => {
          api('/api/avatar/' + encodeURIComponent(perfil.nick) + '/profile?produtos=30')
            .then((novo) => {
              const novosItens = ((novo.outfit || {}).itens || []).length;
              if (!novosItens) return;
              state.completo = novo;
              renderOutfit(novo);
              renderDetalhes(novo);
              renderPerfilCompleto(novo);
            })
            .catch(() => null);
        }, 5000);
      }
    } else {
      state.completo = null;
      $('#avatar-outfit').innerHTML = '<p class="empty">Outfit indisponivel agora.</p>';
      $('#avatar-details').innerHTML = '<p class="empty">Detalhes publicos indisponiveis agora.</p>';
      $('#outfit-info').textContent = '';
    }
  } catch (erro) {
    toast(erro.message, true);
    $('#avatar-card').innerHTML = '<div class="empty big">' + esc(erro.message) + '</div>';
    $('#avatar-metrics').innerHTML = '';
    $('#avatar-timeline').innerHTML = '';
    $('#avatar-rooms').innerHTML = '';
    $('#avatar-looks').innerHTML = '';
    $('#avatar-outfit').innerHTML = '<p class="empty">Consulte um nick para ver o outfit.</p>';
    $('#avatar-details').innerHTML = '<p class="empty">Consulte um nick para ver os detalhes.</p>';
    $('#outfit-info').textContent = '';
  } finally {
    botao.disabled = false;
    botao.textContent = 'Consultar';
  }
}

function renderPerfil(perfil) {
  const imagem = perfil.avatar_3d || perfil.foto_perfil;
  const badges = [];
  badges.push('<span class="badge ' + (perfil.online ? 'online' : 'offline') + '">' +
    (perfil.online ? 'ONLINE' : 'OFFLINE') + '</span>');
  if (perfil.availability) badges.push('<span class="badge">' + esc(perfil.availability) + '</span>');
  if (perfil.aparente_offline) badges.push('<span class="badge warn">possivel modo offline</span>');
  (perfil.marcas || []).forEach((marca) => badges.push('<span class="badge">' + esc(marca) + '</span>'));

  const previsao = perfil.previsao || {};
  const sala = perfil.sala
    ? esc(perfil.sala.nome) + ' (' + esc(perfil.sala.privacidade || '?') + ')'
    : 'nao exposta agora';
  const foto = imagem
    ? '<img class="avatar-img" src="' + esc(imagem) + '" alt="avatar" onerror="this.style.display=\'none\'">'
    : '<div class="avatar-img" style="display:grid;place-items:center;color:rgba(255,255,255,.42);font-weight:700">' +
      esc((perfil.nick || '?').slice(0, 2).toUpperCase()) + '</div>';

  $('#avatar-card').innerHTML =
    '<div class="profile-top">' + foto +
    '<div><div class="profile-name">' + esc(perfil.display_name || perfil.nick) + '</div>' +
    '<div class="profile-sub">@' + esc(perfil.nick) + ' - CID ' + esc(perfil.cid || '?') + '</div>' +
    badges.join('') + '</div></div>' +
    '<div class="profile-facts">' +
    '<div>Sala atual: <b>' + sala + '</b></div>' +
    '<div>Pais: <b>' + esc(perfil.pais || '?') + '</b> - Membro desde <b>' + esc(perfil.registrado || '?') + '</b></div>' +
    '<div>Seguidores: <b>' + esc(perfil.seguidores ?? '?') + '</b> - Ultima vez: <b>' + esc(fmtData(perfil.ultima_vez || perfil.atualizado_em)) + '</b></div>' +
    (previsao.proxima ? '<div>Previsao: <b>' + esc(fmtHora(previsao.proxima)) + '</b></div>' : '') +
    '</div>' +
    (perfil.tagline ? '<div class="tagline">' + esc(perfil.tagline) + '</div>' : '') +
    '<div class="metrics" style="margin-top:16px">' + metricas([
      { rotulo: 'Eventos', valor: perfil.total_eventos ?? '--' },
      { rotulo: 'Primeira vez', valor: perfil.primeira_vez ? fmtData(perfil.primeira_vez).split(',')[0] : '--' },
      { rotulo: 'Fonte', valor: 'IMVU publico' },
    ]) + '</div>';

  $('#avatar-updated').textContent = 'atualizado ' + fmtData(perfil.atualizado_em);
}

function fmtCreditos(valor) {
  const numero = Number(valor);
  if (!valor || Number.isNaN(numero)) return '--';
  return numero.toLocaleString('pt-BR') + ' cr';
}

function produtoCard(item) {
  const imagem = item.imagem
    ? '<img src="' + esc(item.imagem) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
    : '<span class="product-placeholder">sem imagem</span>';
  const desconto = item.preco_desconto && item.preco && item.preco_desconto < item.preco;
  const preco = desconto
    ? '<s>' + esc(fmtCreditos(item.preco)) + '</s> ' + esc(fmtCreditos(item.preco_desconto))
    : esc(fmtCreditos(item.preco));
  return (
    '<a class="product-card" href="' + esc(item.pagina || '#') + '"' + LINK_ALVO + '>' +
    '<div class="product-img">' + imagem + '</div>' +
    '<div class="product-body">' +
    '<div class="product-name" title="' + esc(item.nome) + '">' + esc(item.nome) + '</div>' +
    '<div class="product-meta">' + esc(item.criador || '?') + (item.rating ? ' - ' + esc(item.rating) + (/^\d/.test(String(item.rating)) ? ' estrelas' : '') : '') + '</div>' +
    '<div class="product-price">' + preco + '</div>' +
    '</div></a>'
  );
}

function renderOutfit(dados) {
  const alvo = $('#avatar-outfit');
  const info = $('#outfit-info');
  if (!alvo) return;
  const pacote = (dados && dados.outfit) || dados || {};
  const itens = pacote.itens || [];
  if (!itens.length) {
    alvo.innerHTML = '<p class="empty">Sem itens publicos no outfit agora.</p>';
    if (info) info.textContent = '';
    return;
  }
  if (info) info.textContent = itens.length + ' itens visiveis';
  alvo.innerHTML = itens.map(produtoCard).join('');
}

function renderDetalhes(dados) {
  const alvo = $('#avatar-details');
  if (!alvo) return;
  const perfil = dados.perfil || {};
  const det = dados.detalhes || {};
  const sala = dados.sala || {};
  const linhas = [
    '<div>Idade: <b>' + esc(det.idade ?? '?') + '</b> - Genero: <b>' + esc(det.genero || '?') + '</b></div>',
    '<div>Pais: <b>' + esc(det.pais || '?') + '</b>' + (det.estado ? ' - Estado: <b>' + esc(det.estado) + '</b>' : '') + '</div>',
    '<div>Membro desde: <b>' + esc(det.membro_desde || '?') + '</b> - Selo: <b>' + esc(det.badge_level || 0) + '</b></div>',
    '<div>Seguidores: <b>' + esc(perfil.seguidores ?? '?') + '</b> - Seguindo: <b>' + esc(perfil.seguindo ?? '?') + '</b></div>',
    '<div>Verificado: <b>' + (det.idade_verificada ? 'sim' : 'nao') + '</b> - NFT: <b>' + (det.nft ? 'sim' : 'nao') + '</b></div>',
  ];
  if (sala && sala.rid) {
    linhas.push(
      '<div>Sala atual: <b>' + esc(sala.nome || '?') + '</b>' +
      (sala.online !== null && sala.online !== undefined
        ? ' - <b>' + esc(sala.online) + '</b> de <b>' + esc(sala.capacidade || '?') + '</b> online'
        : '') + '</div>'
    );
    if (sala.dono) linhas.push('<div>Dono da sala: <b>' + esc(sala.dono) + '</b></div>');
  } else {
    linhas.push('<div>Sala atual: <b>nao exposta agora</b></div>');
  }
  let html = '<div class="profile-facts">' + linhas.join('') + '</div>';
  if (sala && sala.imagem) {
    html += '<a class="room-photo" href="' + esc(sala.join_url || '#') + '"' + LINK_ALVO + '>' +
      '<img src="' + esc(sala.imagem) + '" alt="sala atual" loading="lazy" onerror="this.style.display=\'none\'">' +
      (sala.join_url ? '<span>abrir sala no IMVU</span>' : '') + '</a>';
  }
  if (det.interesses) html += '<div class="tagline">' + esc(det.interesses) + '</div>';
  if (sala && sala.descricao) html += '<p class="room-desc">' + esc(sala.descricao) + '</p>';
  alvo.innerHTML = html;
}

function renderPerfilCompleto(dados) {
  if (!state.avatar || !dados) return;
  const perfil = dados.perfil || {};
  if (perfil.seguidores !== undefined && perfil.seguidores !== null) {
    state.avatar.seguidores = perfil.seguidores;
  }
  if (dados.sala) state.avatar.sala = dados.sala;
  renderPerfil(state.avatar);
}

function renderPrevisao(previsao) {
  const alvo = $('#avatar-prediction');
  if (!previsao || !previsao.proxima) {
    alvo.innerHTML = '<p class="empty">' +
      esc(previsao && previsao.aviso ? previsao.aviso : 'Sem dados suficientes para prever.') + '</p>';
    return;
  }
  const confianca = Math.round((Number(previsao.confianca) || 0) * 100);
  const janelas = (previsao.janelas || []).map((janela) => (
    '<div class="window-row"><span>' + esc(janela.dia_semana) + ' as ' + esc(janela.hora) + 'h</span>' +
    '<span>' + Math.round((Number(janela.forca) || 0) * 100) + '%</span></div>'
  )).join('');
  alvo.innerHTML =
    '<div class="prediction-main">Provavel em ' + esc(fmtHora(previsao.proxima)) + '</div>' +
    '<div class="prediction-bar"><span style="width:' + Math.max(4, confianca) + '%"></span></div>' +
    '<div class="hint">confianca ' + confianca + '% - ' + esc(previsao.amostras || 0) + ' amostras</div>' +
    (janelas ? '<div class="window-list">' + janelas + '</div>' : '');
}

function renderHistorico(dados) {
  const stats = dados.estatisticas || {};
  $('#avatar-metrics').innerHTML = metricas([
    { rotulo: 'Tempo online', valor: fmtMinutos(stats.minutos_online), extra: 'ultimos 7 dias' },
    { rotulo: 'Recorde online', valor: fmtMinutos(stats.recorde_online_min) },
    { rotulo: 'Recorde offline', valor: fmtMinutos(stats.recorde_offline_min) },
    { rotulo: 'Ultima vez', valor: fmtData(stats.ultima_vez) },
    { rotulo: 'Sala favorita', valor: stats.sala_mais_frequentada ? stats.sala_mais_frequentada.nome : '--' },
  ]);
  renderPrevisao(dados.previsao);

  const dias = (dados.grafico && dados.grafico.dias) || [];
  desenharGrafico('chart-dias', 'bar', dias.map((d) => d.dia.slice(5)), [{
    label: 'minutos',
    data: dias.map((d) => d.minutos),
    backgroundColor: 'rgba(220,38,38,.55)',
    borderColor: '#dc2626',
    borderWidth: 1,
    borderRadius: 4,
  }]);

  const horas = (dados.grafico && dados.grafico.horas) || [];
  desenharGrafico('chart-horas', 'line', horas.map((_, i) => i + 'h'), [{
    label: 'entradas',
    data: horas,
    borderColor: '#deb359',
    backgroundColor: 'rgba(222,179,89,.12)',
    fill: true,
    tension: .35,
    pointRadius: 0,
  }]);

  const eventos = dados.eventos || [];
  const alvo = $('#avatar-timeline');
  if (!eventos.length) {
    alvo.innerHTML = '<p class="empty">Sem eventos registrados.</p>';
  } else {
    alvo.innerHTML = eventos.map((evento) => (
      '<div class="timeline-item">' +
      '<span class="timeline-dot ' + esc(evento.tipo) + '"></span>' +
      '<span>' + esc(TIPOS[evento.tipo] || evento.tipo) +
      (evento.sala ? ' <span class="room-meta">- ' + esc(evento.sala) + '</span>' : '') + '</span>' +
      '<span class="timeline-when">' + esc(fmtData(evento.ts)) + '</span>' +
      '</div>'
    )).join('');
  }

  const salas = dados.salas || [];
  $('#avatar-rooms').innerHTML = salas.length
    ? salas.map((sala) => (
      '<div class="room-row" style="cursor:default"><div class="room-name">' + esc(sala.nome) +
      '</div><span class="room-meta">' + esc(sala.vezes) + ' eventos</span><span></span></div>'
    )).join('')
    : '<p class="empty">Sem salas registradas.</p>';
}

function renderHistoricoVazio(mensagem) {
  $('#avatar-metrics').innerHTML = '<p class="empty">' + esc(mensagem) + '</p>';
  $('#avatar-timeline').innerHTML = '<p class="empty">Sem eventos.</p>';
  $('#avatar-rooms').innerHTML = '<p class="empty">Sem dados.</p>';
}

function renderLooks(looks) {
  const alvo = $('#avatar-looks');
  if (!looks.length) {
    alvo.innerHTML = '<p class="empty">Nenhum look registrado ainda.</p>';
    return;
  }
  alvo.innerHTML = looks.map((look) => (
    '<div class="look-card">' +
    (look.imagem
      ? '<img src="' + esc(look.imagem) + '" alt="look" loading="lazy" onerror="this.style.display=\'none\'">'
      : '<div class="look-placeholder">sem imagem</div>') +
    '<div class="look-info">' + esc(look.legenda || 'look') + '<br><span class="room-meta">' +
    esc(fmtData(look.quando)) + '</span></div>' +
    '</div>'
  )).join('');
}

function desenharGrafico(id, tipo, labels, datasets) {
  if (!window.Chart) return;
  const canvas = document.getElementById(id);
  if (!canvas) return;
  if (state.charts[id]) state.charts[id].destroy();
  state.charts[id] = new Chart(canvas, {
    type: tipo,
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,.06)' }, ticks: { color: 'rgba(255,255,255,.45)', maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,.06)' }, ticks: { color: 'rgba(255,255,255,.45)' } },
      },
    },
  });
}

async function alternarWatch() {
  if (!state.nick) return;
  const naLista = state.watch.some((w) => String(w.nick).toLowerCase() === String(state.nick).toLowerCase());
  try {
    if (naLista) {
      await api('/api/watch/' + encodeURIComponent(state.nick) + '?device=' + state.device, { method: 'DELETE' });
      toast('Avatar removido da lista.');
    } else {
      await api('/api/watch', {
        method: 'POST',
        body: JSON.stringify({ device: state.device, nick: state.nick, notificar: true }),
      });
      pedirNotificacao();
      toast('Agora voce vai receber alertas de ' + state.nick + '.');
    }
    await carregarWatch();
    atualizarBotaoWatch();
  } catch (erro) {
    toast(erro.message, true);
  }
}

function atualizarBotaoWatch() {
  const botao = $('#watch-btn');
  if (!botao) return;
  const naLista = state.nick && state.watch.some(
    (w) => String(w.nick).toLowerCase() === String(state.nick).toLowerCase()
  );
  botao.textContent = naLista ? 'Acompanhando' : 'Acompanhar';
  botao.className = naLista ? 'primary' : 'ghost';
  botao.disabled = !state.nick;
}

async function carregarWatch() {
  try {
    const dados = await api('/api/watch?device=' + state.device);
    state.watch = dados.avatares || [];
    renderWatch();
    atualizarBotaoWatch();
  } catch (erro) {
    $('#watch-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function renderWatch() {
  const alvo = $('#watch-list');
  if (!alvo) return;
  if (!state.watch.length) {
    alvo.innerHTML = '<p class="empty">Nenhum avatar na lista. Consulte um nick e toque em Acompanhar.</p>';
    return;
  }
  alvo.innerHTML = state.watch.map((avatar) => {
    const sala = avatar.sala ? avatar.sala.nome : 'sem sala exposta';
    const quando = avatar.previsao && avatar.previsao.proxima ? 'provavel ' + fmtHora(avatar.previsao.proxima) : 'sem previsao';
    return '<div class="watch-row">' +
      '<div><b>' + esc(avatar.nick) + '</b>' +
      '<small>' + (avatar.online ? 'online' : 'offline') + ' - ' + esc(sala) + '</small>' +
      '<small>' + esc(quando) + '</small></div>' +
      '<button type="button" class="ghost small" data-remove="' + esc(avatar.nick) + '">Remover</button>' +
      '</div>';
  }).join('');
}

$('#watch-list').addEventListener('click', async (evento) => {
  const botao = evento.target.closest('[data-remove]');
  if (!botao) return;
  const nick = botao.dataset.remove;
  try {
    await api('/api/watch/' + encodeURIComponent(nick) + '?device=' + state.device, { method: 'DELETE' });
    toast('Removido: ' + nick);
    await carregarWatch();
  } catch (erro) { toast(erro.message, true); }
});

async function carregarAlertas() {
  try {
    const dados = await api('/api/alerts?device=' + state.device + '&limite=40');
    state.alerts = dados.alertas || [];
    const alvo = $('#alert-list');
    if (!state.alerts.length) {
      alvo.innerHTML = '<p class="empty">Sem alertas ainda.</p>';
      return;
    }
    alvo.innerHTML = state.alerts.map((alerta) => (
      '<div class="feed-item"><span class="feed-time">' + esc(fmtData(alerta.quando)) + '</span>' +
      '<b>' + esc(alerta.titulo) + '</b>' +
      (alerta.corpo ? '<div>' + esc(alerta.corpo) + '</div>' : '') + '</div>'
    )).join('');
  } catch (erro) {
    $('#alert-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

$('#mark-seen').addEventListener('click', async () => {
  const ids = state.alerts.filter((a) => !a.visto).map((a) => a.id);
  if (!ids.length) { toast('Nada para marcar.'); return; }
  try {
    await api('/api/alerts/seen', {
      method: 'POST',
      body: JSON.stringify({ device: state.device, ids }),
    });
    toast('Alertas marcados como vistos.');
    carregarAlertas();
  } catch (erro) { toast(erro.message, true); }
});

function configurarHooks() {
  const kind = $('#hook-kind');
  const wrap = $('#hook-chat-wrap');
  const atualizar = () => { wrap.style.display = kind.value === 'telegram' ? 'flex' : 'none'; };
  kind.addEventListener('change', atualizar);
  atualizar();

  $('#hook-form').addEventListener('submit', async (evento) => {
    evento.preventDefault();
    const eventos = $$('.events input:checked').map((input) => input.value);
    const payload = {
      device: state.device,
      tipo: $('#hook-kind').value,
      url: $('#hook-url').value.trim(),
      chat_id: $('#hook-chat').value.trim() || null,
      eventos,
    };
    try {
      await api('/api/webhooks', { method: 'POST', body: JSON.stringify(payload) });
      toast('Webhook adicionado.');
      $('#hook-url').value = '';
      $('#hook-chat').value = '';
      carregarHooks();
    } catch (erro) { toast(erro.message, true); }
  });

  $('#hook-list').addEventListener('click', async (evento) => {
    const botao = evento.target.closest('[data-hook]');
    if (!botao) return;
    try {
      await api('/api/webhooks/' + botao.dataset.hook + '?device=' + state.device, { method: 'DELETE' });
      toast('Webhook removido.');
      carregarHooks();
    } catch (erro) { toast(erro.message, true); }
  });
}

async function carregarHooks() {
  try {
    const dados = await api('/api/webhooks?device=' + state.device);
    state.hooks = dados.webhooks || [];
    const alvo = $('#hook-list');
    if (!state.hooks.length) {
      alvo.innerHTML = '<p class="empty">Nenhum webhook configurado.</p>';
      return;
    }
    alvo.innerHTML = state.hooks.map((hook) => (
      '<div class="watch-row"><div><b>' + esc(hook.tipo) + '</b>' +
      '<small>' + esc(hook.url) + '</small>' +
      '<small>' + (hook.eventos.length ? hook.eventos.join(', ') : 'todos os eventos') + '</small></div>' +
      '<button type="button" class="ghost small danger" data-hook="' + esc(hook.id) + '">Excluir</button></div>'
    )).join('');
  } catch (erro) {
    $('#hook-list').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function configurarSalas() {
  $('#rid-btn').addEventListener('click', () => abrirSala($('#rid-input').value));
  $('#rid-input').addEventListener('keydown', (evento) => {
    if (evento.key === 'Enter') abrirSala($('#rid-input').value);
  });
  const select = $('#letra-select');
  const letras = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'.split('');
  select.innerHTML = letras.map((letra) => '<option value="' + letra + '">' + letra + '</option>').join('');
  $('#letra-btn').addEventListener('click', () => carregarDiretorio(select.value));
  const vivo = $('#vivo-letra');
  vivo.innerHTML = letras.map((letra) => '<option value="' + letra + '">' + letra + '</option>').join('');
  $('#vivo-btn').addEventListener('click', () => carregarSalasAoVivo(vivo.value));
  $('#rooms-live').addEventListener('click', (evento) => {
    const card = evento.target.closest('[data-rid]');
    if (!card) return;
    $('#rid-input').value = card.dataset.rid;
    abrirSala(card.dataset.rid);
  });
  $('#rooms-table').addEventListener('click', (evento) => {
    const linha = evento.target.closest('[data-rid]');
    if (linha) abrirSala(linha.dataset.rid);
  });
  $('#directory').addEventListener('click', (evento) => {
    const chip = evento.target.closest('[data-rid]');
    if (chip) abrirSala(chip.dataset.rid);
  });
}

async function carregarSalas() {
  try {
    const dados = await api('/api/rooms/top?horas=24&limite=20');
    $('#rooms-table').innerHTML = htmlSalas(dados.salas, 'Nenhuma sala monitorada ainda.');
  } catch (erro) {
    $('#rooms-table').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
  carregarSalasAoVivo($('#vivo-letra').value || 'A');
  try {
    const calor = await api('/api/rooms/heatmap?horas=168');
    desenharHeatmap($('#heatmap-salas'), calor);
  } catch (_) { /* opcional */ }
}

function liveCard(sala) {
  const imagem = sala.imagem
    ? '<img src="' + esc(sala.imagem) + '" alt="" loading="lazy" onerror="this.style.display=\'none\'">'
    : '<span class="live-placeholder">sem imagem</span>';
  return (
    '<button type="button" class="live-card" data-rid="' + esc(sala.rid) + '" title="' + esc(sala.nome) + '">' +
    '<div class="live-img">' + imagem + '</div>' +
    '<div class="live-body">' +
    '<div class="room-name">' + esc(sala.nome || 'Sala sem nome') + '</div>' +
    '<div class="room-meta">' + esc(sala.dono || '?') + ' - ' + esc(sala.privacidade || 'publica') +
    (sala.idioma ? ' - ' + esc(sala.idioma) : '') + '</div>' +
    '<div class="live-count">' + esc(sala.online ?? '?') +
    '<span>/' + esc(sala.capacidade || '?') + ' online</span></div>' +
    '</div></button>'
  );
}

async function carregarSalasAoVivo(letra) {
  const alvo = $('#rooms-live');
  if (!alvo) return;
  alvo.innerHTML = '<div class="skeleton"></div>';
  try {
    const dados = await api('/api/rooms/live?letra=' + encodeURIComponent(letra || 'A') + '&limite=12');
    const salas = dados.salas || [];
    alvo.innerHTML = salas.length
      ? salas.map(liveCard).join('')
      : '<p class="empty">Nenhuma sala publica encontrada para ' + esc(letra) + '.</p>';
  } catch (erro) {
    alvo.innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

async function abrirSala(rid) {
  const valor = String(rid || '').trim();
  if (!valor) { toast('Informe o ID da sala.', true); return; }
  $('#room-detail').innerHTML = '<div class="skeleton"></div>';
  const [publico, historico] = await Promise.allSettled([
    api('/api/rooms/' + encodeURIComponent(valor)),
    api('/api/rooms/' + encodeURIComponent(valor) + '/history?horas=24'),
  ]);
  if (publico.status === 'rejected' && historico.status === 'rejected') {
    const erro = publico.reason || historico.reason || new Error('sala indisponivel');
    toast(erro.message, true);
    $('#room-detail').innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
    return;
  }
  const sala = publico.status === 'fulfilled' ? publico.value : (historico.value.sala || {});
  const dados = historico.status === 'fulfilled' ? historico.value : {};
  const resumo = dados.resumo || {};
  $('#rid-input').value = sala.rid || valor;
  let html = '';
  if (sala.imagem) {
    html += '<a class="room-photo" href="' + esc(sala.join_url || '#') + '"' + LINK_ALVO + '>' +
      '<img src="' + esc(sala.imagem) + '" alt="sala" loading="lazy" onerror="this.style.display=\'none\'">' +
      (sala.join_url ? '<span>abrir sala no IMVU</span>' : '') + '</a>';
  }
  html += '<div class="profile-facts">' +
    '<div><b>' + esc(sala.nome || 'Sala') + '</b> - ' + esc(sala.rid || valor) + '</div>' +
    '<div>Dono: <b>' + esc(sala.dono || '?') + '</b> - Privacidade: <b>' + esc(sala.privacidade || '?') + '</b></div>' +
    '<div>Agora: <b>' + esc(sala.online ?? resumo.online_agora ?? '?') + '</b> de <b>' +
    esc(sala.capacidade || '?') + '</b> online</div>' +
    (sala.rating !== undefined && sala.rating !== null
      ? '<div>Avaliacao: <b>' + esc(sala.rating) + '</b>' +
        (sala.idioma ? ' - Idioma: <b>' + esc(sala.idioma) + '</b>' : '') + '</div>'
      : '') +
    (resumo.media !== undefined
      ? '<div>Media: <b>' + esc(resumo.media) + '</b> - Pico: <b>' + esc(resumo.pico ?? '?') + '</b></div>'
      : '') +
    (resumo.melhor_horario ? '<div>Melhor horario: <b>' + esc(resumo.melhor_horario.hora) + 'h</b></div>' : '') +
    '</div>';
  if (sala.descricao) html += '<p class="room-desc">' + esc(sala.descricao) + '</p>';
  $('#room-detail').innerHTML = html;
  const pontos = dados.pontos || [];
  if (pontos.length) {
    desenharGrafico('chart-sala', 'line', pontos.map((p) => fmtData(p.ts).slice(-5)), [{
      label: 'online',
      data: pontos.map((p) => p.online),
      borderColor: '#dc2626',
      backgroundColor: 'rgba(220,38,38,.12)',
      fill: true,
      tension: .3,
      pointRadius: 0,
    }]);
  } else {
    $('#room-detail').innerHTML += '<p class="empty">Sem historico local ainda; mostrando dados ao vivo do IMVU.</p>';
  }
}

async function carregarDiretorio(letra) {
  const alvo = $('#directory');
  alvo.innerHTML = '<div class="skeleton"></div>';
  try {
    const dados = await api('/api/rooms/directory?letra=' + encodeURIComponent(letra));
    const salas = dados.salas || [];
    alvo.innerHTML = salas.length
      ? salas.map((sala) => '<button type="button" class="chip" data-rid="' + esc(sala.rid) + '">' +
        esc(sala.nome) + ' - ' + esc(sala.rid) + '</button>').join('')
      : '<p class="empty">Nenhuma sala encontrada para ' + esc(letra) + '.</p>';
  } catch (erro) {
    alvo.innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
  }
}

function configurarCatalogo() {
  const form = $('#catalog-form');
  if (!form) return;
  form.addEventListener('submit', (evento) => {
    evento.preventDefault();
    buscarCatalogo(1);
  });
  $('#catalog-pager').addEventListener('click', (evento) => {
    const botao = evento.target.closest('[data-pagina]');
    if (botao) buscarCatalogo(Number(botao.dataset.pagina));
  });
}

async function buscarCatalogo(pagina) {
  const termo = $('#catalog-q').value.trim();
  if (!termo) { toast('Digite um termo para buscar.', true); return; }
  const tipo = $('#catalog-type').value;
  const ordenar = $('#catalog-sort').value;
  const alvo = $('#catalog-grid');
  alvo.innerHTML = '<div class="skeleton"></div>';
  $('#catalog-info').textContent = 'buscando...';
  try {
    const dados = await api(
      '/api/catalog/search?q=' + encodeURIComponent(termo) +
      '&pagina=' + Math.max(1, Number(pagina) || 1) +
      '&ordenar=' + encodeURIComponent(ordenar) +
      '&tipo=' + encodeURIComponent(tipo)
    );
    state.catalog = { q: termo, tipo, ordenar, pagina: dados.pagina };
    renderCatalogo(dados);
  } catch (erro) {
    toast(erro.message, true);
    alvo.innerHTML = '<p class="empty">' + esc(erro.message) + '</p>';
    $('#catalog-info').textContent = '';
    $('#catalog-pager').innerHTML = '';
  }
}

function renderCatalogo(dados) {
  const itens = dados.itens || [];
  $('#catalog-info').textContent =
    (itens.length ? itens.length + ' itens' : 'nenhum item') +
    ' - pagina ' + dados.pagina + ' de ' + Math.max(1, dados.paginas || 1);
  $('#catalog-grid').innerHTML = itens.length
    ? itens.map(produtoCard).join('')
    : '<p class="empty">Nada encontrado para "' + esc(dados.termo || '') + '".</p>';
  const pager = $('#catalog-pager');
  if (!itens.length || (dados.paginas || 1) <= 1) {
    pager.innerHTML = '';
    return;
  }
  const anterior = dados.pagina > 1
    ? '<button type="button" class="chip" data-pagina="' + (dados.pagina - 1) + '">Anterior</button>'
    : '';
  const proxima = dados.pagina < dados.paginas
    ? '<button type="button" class="chip" data-pagina="' + (dados.pagina + 1) + '">Proxima</button>'
    : '';
  pager.innerHTML = anterior + '<span class="hint">pagina ' + dados.pagina + ' de ' + dados.paginas + '</span>' + proxima;
}

function anexarFeed(evento) {
  const alvo = $('#live-feed');
  if (!alvo) return;
  if (alvo.querySelector('.empty')) alvo.innerHTML = '';
  let texto = '';
  if (evento.tipo === 'status') {
    const acao = (evento.eventos || []).includes('online') ? 'entrou online'
      : (evento.eventos || []).includes('offline') ? 'ficou offline'
      : (evento.eventos || []).includes('sala') ? 'mudou de sala'
      : (evento.eventos || []).includes('look') ? 'trocou de look'
      : 'atualizou';
    texto = '<b>' + esc(evento.nick) + '</b> ' + acao +
      (evento.sala ? ' - ' + esc(evento.sala.nome) : '');
  } else if (evento.tipo === 'sala') {
    texto = 'Sala <b>' + esc(evento.nome || evento.rid) + '</b> com <b>' + esc(evento.online) + '</b> pessoas';
  } else if (evento.tipo === 'alerta') {
    texto = '<b>' + esc(evento.titulo) + '</b>' + (evento.corpo ? ' - ' + esc(evento.corpo) : '');
  } else {
    return;
  }
  const item = document.createElement('div');
  item.className = 'feed-item';
  item.innerHTML = '<span class="feed-time">' + new Date().toLocaleTimeString('pt-BR') + '</span>' + texto;
  alvo.prepend(item);
  while (alvo.children.length > 50) alvo.lastChild.remove();
}

function atualizarPerfilAoVivo(evento) {
  if (!state.nick || String(evento.nick).toLowerCase() !== String(state.nick).toLowerCase()) return;
  if (!state.avatar) return;
  state.avatar.online = evento.online;
  state.avatar.sala = evento.sala;
  const card = $('#avatar-card');
  const badge = card && card.querySelector('.badge.online, .badge.offline');
  if (badge) {
    badge.classList.toggle('online', !!evento.online);
    badge.classList.toggle('offline', !evento.online);
    badge.textContent = evento.online ? 'ONLINE' : 'OFFLINE';
  }
  const fatos = card && card.querySelectorAll('.profile-facts b');
  if (fatos && fatos.length && evento.sala) fatos[0].textContent = evento.sala.nome;
}

function conectarWs() {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) return;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  let ws;
  try {
    ws = new WebSocket(proto + '://' + location.host + '/ws/live');
  } catch (_) {
    setLive(false);
    setTimeout(conectarWs, 5000);
    return;
  }
  state.ws = ws;
  ws.onopen = () => setLive(true);
  ws.onclose = () => {
    setLive(false);
    clearTimeout(state.reconnect);
    state.reconnect = setTimeout(conectarWs, 4000);
  };
  ws.onerror = () => { try { ws.close(); } catch (_) { /* ignora */ } };
  ws.onmessage = (mensagem) => {
    let evento = null;
    try { evento = JSON.parse(mensagem.data); } catch (_) { return; }
    if (!evento || evento.tipo === 'ping') return;
    anexarFeed(evento);
    if (evento.tipo === 'alerta') {
      notificar(evento.titulo, evento.corpo);
      carregarAlertas();
    }
    if (evento.tipo === 'status') atualizarPerfilAoVivo(evento);
  };
}

function pedirNotificacao() {
  if (!('Notification' in window)) return;
  if (Notification.permission === 'default') Notification.requestPermission();
}

function notificar(titulo, corpo) {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    toast(titulo + (corpo ? ' - ' + corpo : ''));
    return;
  }
  try {
    new Notification(titulo, { body: corpo || '', tag: 'imvu-status', silent: false });
  } catch (_) {
    toast(titulo + (corpo ? ' - ' + corpo : ''));
  }
}

function configurarApi() {
  const rotas = [
    ['GET', '/api/status/{nick}', 'status atual, sala e previsao'],
    ['GET', '/api/avatar/{nick}/profile', 'perfil completo com outfit e sala'],
    ['GET', '/api/avatar/{nick}/outfit', 'itens publicos do look atual'],
    ['GET', '/api/avatar/{nick}/history', 'linha do tempo, graficos e recordes'],
    ['GET', '/api/avatar/{nick}/looks', 'galeria de looks'],
    ['GET', '/api/avatar/{nick}/prediction', 'previsao de horarios'],
    ['GET', '/api/catalog/search', 'catalogo de produtos e criadores'],
    ['GET', '/api/rooms/top', 'ranking de salas por ocupacao'],
    ['GET', '/api/rooms/live', 'salas publicas com ocupacao ao vivo'],
    ['GET', '/api/rooms/heatmap', 'movimento por dia e hora'],
    ['GET', '/api/rooms/{rid}', 'detalhes publicos de uma sala'],
    ['GET', '/api/rooms/{rid}/history', 'ocupacao de uma sala'],
    ['GET', '/api/rooms/directory?letra=A', 'diretorio publico de salas'],
    ['GET', '/api/digest', 'resumo automatico do dia'],
    ['GET', '/api/watch?device={id}', 'lista de avatares acompanhados'],
    ['POST', '/api/watch', 'adicionar ou atualizar acompanhamento'],
    ['DELETE', '/api/watch/{nick}', 'remover acompanhamento'],
    ['GET', '/api/alerts?device={id}', 'alertas recebidos'],
    ['GET', '/api/events?device={id}', 'stream SSE em tempo real'],
    ['GET', '/api/webhooks?device={id}', 'webhooks configurados'],
    ['POST', '/api/webhooks', 'criar webhook Discord/Telegram'],
    ['GET', '/api/health', 'saude do sistema'],
    ['GET', '/api/config', 'configuracao publica'],
  ];
  $('#api-table').innerHTML = rotas.map((rota) => (
    '<tr><td>' + rota[0] + '</td><td><code>' + esc(rota[1]) + '</code></td><td>' + esc(rota[2]) + '</td></tr>'
  )).join('');
  const base = location.origin;
  $('#api-examples').textContent =
    'curl ' + base + '/api/status/SEU_NICK\n\n' +
    'curl "' + base + '/api/avatar/SEU_NICK/profile?produtos=30"\n\n' +
    'curl "' + base + '/api/avatar/SEU_NICK/history?horas=168"\n\n' +
    'curl "' + base + '/api/catalog/search?q=dress&pagina=1"\n\n' +
    'curl "' + base + '/api/rooms/live?letra=A&limite=12"\n\n' +
    'curl "' + base + '/api/rooms/top?horas=24"\n\n' +
    'curl "' + base + '/api/events?device=' + state.device + '"\n\n' +
    'Documentacao interativa: ' + base + '/docs';
}

function iniciar() {
  state.device = deviceId();
  $('#device-pill').textContent = 'aparelho ' + state.device.slice(0, 6);
  configurarNavegacao();
  configurarAvatar();
  configurarSalas();
  configurarCatalogo();
  configurarHooks();
  configurarApi();
  relogio();
  setInterval(relogio, 30000);
  carregarConfig();
  carregarPainel();
  carregarWatch();
  conectarWs();
  setInterval(carregarWatch, 45000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', iniciar);
} else {
  iniciar();
}
