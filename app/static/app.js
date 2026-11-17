'use strict';

const $ = (sel) => document.querySelector(sel);
const brl = (n) => n.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const dataCurta = (iso) => iso ? new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }) : '';
const horaCurta = (iso) => iso ? new Date(iso).toLocaleString('pt-BR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';

const ETAPAS = {
  fila: 'na fila',
  conferindo: 'conferindo',
  emitindo: 'emitindo NF',
  autorizando: 'aguardando SEFAZ',
  aguardando_etiqueta: 'aguardando etiqueta',
  pronta: 'pronta',
  erro: 'erro',
};

let vendas = [];
const marcadas = new Set();
let jaCarregou = false;
let pollJob = null;

function el(tag, attrs = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? '' : v);
  }
  for (const f of filhos.flat()) {
    if (f === null || f === undefined || f === false) continue;
    e.append(f instanceof Node ? f : document.createTextNode(String(f)));
  }
  return e;
}

async function api(url, opcoes = {}) {
  const r = await fetch(url, {
    ...opcoes,
    headers: { 'Content-Type': 'application/json', ...(opcoes.headers || {}) },
  });
  const corpo = await r.json().catch(() => null);
  if (!r.ok) throw new Error((corpo && corpo.erro) || `Erro ${r.status}`);
  return corpo;
}

// ------------------------------------------------------------------ lista

async function carregarVendas(forcar = false) {
  $('#atualizado').textContent = 'Atualizando…';
  $('#btn-atualizar').disabled = true;
  try {
    const r = await api('/api/vendas' + (forcar ? '?atualizar=1' : ''));
    const anteriores = new Set(vendas.map((v) => String(v.venda)));
    vendas = r.vendas;
    for (const v of vendas) {
      const id = String(v.venda);
      if (v.em_processamento) marcadas.delete(id);
      else if (!jaCarregou || !anteriores.has(id)) marcadas.add(id);  // novas vêm marcadas
    }
    for (const id of [...marcadas]) if (!vendas.some((v) => String(v.venda) === id)) marcadas.delete(id);
    jaCarregou = true;
    $('#atualizado').textContent = 'Atualizado às ' + new Date(r.atualizado_em * 1000).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' });
  } catch (err) {
    $('#atualizado').textContent = 'Falha ao atualizar: ' + err.message;
  } finally {
    $('#btn-atualizar').disabled = false;
  }
  desenharLista();
}

function desenharLista() {
  const nf = vendas.filter((v) => v.situacao === 'nf_pendente').length;
  const prontas = vendas.length - nf;
  $('#resumo').replaceChildren(
    el('span', { class: 'chip nf' }, `${nf} aguardando NF`),
    el('span', { class: 'chip pronta' }, `${prontas} prontas para imprimir`),
  );

  const lista = $('#lista');
  lista.replaceChildren(...vendas.map((v) => {
    const id = String(v.venda);
    const travada = v.em_processamento;
    const check = el('input', { type: 'checkbox', disabled: travada, checked: marcadas.has(id) && !travada });
    check.addEventListener('change', () => { check.checked ? marcadas.add(id) : marcadas.delete(id); atualizarBarra(); });
    const card = el('div', { class: 'venda' + (travada ? ' travada' : '') },
      check,
      el('div', {}, el('div', { class: 'nome' }, v.comprador), el('div', { class: 'sutil' }, `${v.apelido || ''} · venda ${id}`)),
      el('div', { class: 'lado' }, el('div', {}, brl(v.total)), el('div', { class: 'sutil' }, v.entrega ? 'entrega ' + dataCurta(v.entrega) : '')),
      el('div', { class: 'itens' }, v.itens.map((it) => el('div', {}, `${it.qtd}x `, el('strong', {}, it.sku || '(sem SKU)'), el('span', { class: 'sutil' }, ` — ${it.titulo}`)))),
      el('div', { class: 'meta' },
        el('span', { class: 'tag ' + (v.situacao === 'nf_pendente' ? 'nf' : 'pronta') }, v.situacao === 'nf_pendente' ? 'Aguardando NF' : 'Pronta para imprimir'),
        v.b2b ? el('span', { class: 'tag b2b' }, 'B2B') : null,
        travada ? el('span', { class: 'tag proc' }, 'Em processamento') : null,
        el('span', { class: 'sutil' }, 'vendida ' + horaCurta(v.data)),
      ),
    );
    card.addEventListener('click', (e) => {
      if (travada || e.target === check) return;
      check.checked = !check.checked;
      check.dispatchEvent(new Event('change'));
    });
    return card;
  }));
  $('#vazio').hidden = vendas.length > 0;
  atualizarBarra();
}

function selecionaveis() {
  return vendas.filter((v) => !v.em_processamento);
}

function atualizarBarra() {
  const sel = selecionaveis().filter((v) => marcadas.has(String(v.venda)));
  const todas = selecionaveis();
  $('#check-todos').checked = todas.length > 0 && sel.length === todas.length;
  $('#check-todos').indeterminate = sel.length > 0 && sel.length < todas.length;
  $('#selecionadas').textContent = sel.length ? `${sel.length} selecionada(s)` : '';
  $('#btn-processar').textContent = sel.length ? `Processar ${sel.length}` : 'Processar';
  $('#btn-processar').disabled = sel.length === 0 || pollJob !== null;
}

$('#check-todos').addEventListener('change', (e) => {
  for (const v of selecionaveis()) e.target.checked ? marcadas.add(String(v.venda)) : marcadas.delete(String(v.venda));
  desenharLista();
});
$('#btn-atualizar').addEventListener('click', () => { carregarVendas(true); carregarHistorico(); });

// ------------------------------------------------------------------ processar

function abrir(id) { $(id).hidden = false; }
function fechar(id) { $(id).hidden = true; }
document.querySelectorAll('[data-fechar]').forEach((b) => b.addEventListener('click', () => fechar('#' + b.closest('.modal').id)));

$('#btn-processar').addEventListener('click', () => {
  const sel = selecionaveis().filter((v) => marcadas.has(String(v.venda)));
  const nf = sel.filter((v) => v.situacao === 'nf_pendente').length;
  $('#confirmar-texto').textContent =
    `${sel.length} venda(s): ${nf} NF-e serão emitidas (não dá para desfazer) e ` +
    `${sel.length} etiqueta(s) serão baixadas, marcando como impressas no Mercado Livre.`;
  abrir('#modal-confirmar');
});

$('#btn-confirmar').addEventListener('click', async () => {
  const ids = selecionaveis().filter((v) => marcadas.has(String(v.venda))).map((v) => String(v.venda));
  $('#btn-confirmar').disabled = true;
  try {
    const job = await api('/api/processar', { method: 'POST', body: JSON.stringify({ vendas: ids }) });
    fechar('#modal-confirmar');
    mostrarJob(job);
    iniciarPoll();
  } catch (err) {
    alertaInline('#confirmar-texto', err.message);
  } finally {
    $('#btn-confirmar').disabled = false;
  }
});

function alertaInline(sel, msg) {
  const p = $(sel);
  p.replaceChildren(el('span', { class: 'alerta erro' }, msg));
}

function icone(etapa) {
  if (etapa === 'pronta') return el('span', { class: 'icone ok' }, '✓');
  if (etapa === 'erro') return el('span', { class: 'icone falha' }, '✕');
  if (etapa === 'fila') return el('span', { class: 'icone espera' });
  return el('span', { class: 'icone girando' });
}

function mostrarJob(job) {
  const total = job.vendas.length;
  const feitas = job.vendas.filter((v) => v.etapa === 'pronta' || v.etapa === 'erro').length;
  const titulo = job.status === 'gerando' ? 'Gerando os PDFs…' : `Processando ${feitas}/${total}…`;
  $('#proc-titulo').textContent = titulo;
  const inicio = job.iniciado_em || job.criado_em;
  const min = Math.floor((Date.now() - new Date(inicio)) / 60000);
  $('#proc-sub').textContent = `Iniciado às ${new Date(inicio).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}` + (min ? ` · ${min} min` : '');
  $('#proc-lista').replaceChildren(...job.vendas.map((v) => el('li', {},
    icone(v.etapa),
    el('div', {}, el('div', {}, v.comprador), el('div', { class: 'sutil' }, `venda ${v.venda}` + (v.nf ? ` · NF ${v.nf}` : ''))),
    el('span', { class: 'etapa' }, ETAPAS[v.etapa] || v.etapa),
    v.erro ? el('div', { class: 'erro' }, v.erro) : null,
  )));
  abrir('#modal-processando');
}

function iniciarPoll() {
  if (pollJob) return;
  pollJob = setInterval(verificarJob, 3000);
  atualizarBarra();
}

function pararPoll() {
  clearInterval(pollJob);
  pollJob = null;
  atualizarBarra();
}

async function verificarJob() {
  let job;
  try { job = await api('/api/job'); } catch { return; }
  if (!job) { pararPoll(); fechar('#modal-processando'); return; }
  if (['fila', 'processando', 'gerando'].includes(job.status)) {
    mostrarJob(job);
    iniciarPoll();
    return;
  }
  pararPoll();
  fechar('#modal-processando');
  if (job.status === 'concluido' && !job.visto) {
    mostrarResultado(job.resultado || {}, job);
    carregarVendas(true);
    carregarHistorico();
  }
}

// ------------------------------------------------------------------ resultado

let jobDoResultado = null;

function mostrarResultado(res, job = null) {
  jobDoResultado = job;
  const ok = !!res.pasta;
  const erros = res.erros || [];
  $('#res-titulo').textContent = ok ? (erros.length ? 'Pronto, com erros' : 'Pronto') : 'Nenhuma etiqueta gerada';

  const downloads = [];
  if (res.danfe) downloads.push(el('a', { class: 'btn primario', href: `/arquivos/${res.pasta}/${encodeURIComponent(res.danfe)}` }, '⬇ DANFEs'));
  if (res.etiquetas) downloads.push(el('a', { class: 'btn primario', href: `/arquivos/${res.pasta}/${encodeURIComponent(res.etiquetas)}` }, '⬇ Etiquetas + resumo'));
  $('#res-downloads').replaceChildren(...downloads);

  $('#res-erros').replaceChildren(...erros.map((e) => el('div', { class: 'alerta erro' }, (e.comprador ? `${e.comprador} (venda ${e.venda}): ` : '') + e.erro)));
  $('#res-mensagem').value = res.mensagem || '';
  $('#res-mensagem').closest('.caixa').querySelector('h3').hidden = !res.mensagem;
  $('#res-mensagem').hidden = !res.mensagem;
  $('#btn-copiar').hidden = !res.mensagem;

  const est = res.estoque;
  const blocos = [];
  if (est) {
    blocos.push(el('h3', {}, 'Estoque de devoluções'));
    if (est.erro) blocos.push(el('div', { class: 'alerta aviso' }, 'Não consegui consultar o estoque: ' + est.erro));
    else if (!est.exatos.length && !est.parecidos.length) blocos.push(el('p', { class: 'sutil' }, 'Nenhum item das vendas está no estoque de devoluções.'));
    for (const [rotulo, lista] of [['Mesmo SKU', est.exatos], ['Parecido', est.parecidos]]) {
      if (!lista || !lista.length) continue;
      blocos.push(el('div', { class: 'estoque' }, el('div', { class: 'rotulo' }, rotulo),
        el('ul', {}, lista.map((m) => el('li', {}, `${m.levando}x ${m.vendido} (${m.nome}) → tem ${m.total}x ${m.no_estoque}`)))));
    }
  }
  $('#res-estoque').replaceChildren(...blocos);
  $('#res-avisos').replaceChildren(...(res.avisos || []).map((a) => el('div', { class: 'alerta aviso' }, a)));
  abrir('#modal-resultado');
}

$('#btn-fechar-resultado').addEventListener('click', async () => {
  fechar('#modal-resultado');
  if (jobDoResultado) {
    jobDoResultado = null;
    try { await api('/api/job/visto', { method: 'POST', body: '{}' }); } catch { /* sem problema */ }
  }
});

$('#btn-copiar').addEventListener('click', async () => {
  const t = $('#res-mensagem');
  try { await navigator.clipboard.writeText(t.value); }
  catch { t.select(); document.execCommand('copy'); }
  $('#btn-copiar').textContent = 'Copiada ✓';
  setTimeout(() => { $('#btn-copiar').textContent = 'Copiar mensagem'; }, 1500);
});

// ------------------------------------------------------------------ histórico

async function carregarHistorico() {
  let itens = [];
  try { itens = await api('/api/historico'); } catch { /* mantém vazio */ }
  const box = $('#historico');
  if (!itens.length) { box.replaceChildren(el('p', { class: 'sutil' }, 'Nenhum processamento ainda.')); return; }
  box.replaceChildren(...itens.map((h) => el('div', { class: 'hist-item' },
    el('div', {},
      el('div', {}, horaCurta(h.criado_em)),
      el('div', { class: 'sutil' }, `${h.vendas.length} venda(s)` + (h.vendas.some((v) => v.nf) ? ' · NF ' + h.vendas.map((v) => v.nf).filter(Boolean).join(', ') : ''))),
    el('div', { class: 'acoes-hist' },
      h.danfe ? el('a', { class: 'btn', href: `/arquivos/${h.pasta}/${encodeURIComponent(h.danfe)}` }, '⬇ DANFEs') : null,
      h.etiquetas ? el('a', { class: 'btn', href: `/arquivos/${h.pasta}/${encodeURIComponent(h.etiquetas)}` }, '⬇ Etiquetas') : null,
      el('button', { class: 'btn', onclick: () => mostrarResultado(h) }, 'Ver mensagem'),
    ),
  )));
}

// ------------------------------------------------------------------ início

carregarVendas();
carregarHistorico();
verificarJob();
setInterval(() => { if (!pollJob && document.visibilityState === 'visible') carregarVendas(); }, 60000);
