"""JavaScript do painel: KPIs, gráficos SVG e tabelas, sem dependências externas.

Mantido em Python como string para não dividir o painel em vários arquivos no
disco; `build_dashboard.py` injeta isto no HTML.
"""

JS = r"""
const nf = n => n == null ? '—' : n.toLocaleString('pt-BR');
const pct = n => (n == null ? '—' : n.toFixed(2) + '%');
/* Decimal pt-BR: o painel é em português, e `12.14` num KPI de 12%
   de fatia parece erro de arredondamento. */
const dec = (v, d = 2) => v == null ? '—' : v.toFixed(d).replace('.', ',');
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

/* ==================================================== navegacao (drill-down) */
/* Todo numero, nome e linha do painel aponta para um cadastro real. O painel e
   a janela da analise; o registro e a janela do dado, e ela mora no app
   lateral. Como o painel aparece dentro de um <iframe>, a navegacao resolve
   em duas situacoes: dentro do app pede ao pai para abrir a rota; sozinho (o
   arquivo aberto direto no navegador) vai para a mesma rota por fragmento, e o
   app le o fragmento no arranque. Nenhum dos dois caminhos duplica a tela. */

const CATALOGOS = {
  empresa: 'Empresas', usuario: 'Usuarios', aeronave: 'Aeronaves',
  fabricante: 'Fabricantes', modelo: 'Modelos', marca: 'Marcas',
  aerodromo: 'Aerodromos', drone: 'Drones (SISANT)', vinculo: 'Vinculos',
};

/* Destino de uma linha: {cat, chave, q}. `chave` ausente vira busca. */
function abrir(destino){
  if (!destino || !destino.cat) return;
  const alvo = {cat: destino.cat,
                chave: destino.chave == null ? null : String(destino.chave),
                q: destino.q || ''};
  try {
    if (window.parent && window.parent !== window && window.parent.abrirDetalhe){
      window.parent.abrirDetalhe(alvo);
      return;
    }
  } catch (e) { /* pai de outra origem: cai para o fragmento */ }
  location.href = '/app' + rotaHash(alvo);
}

function rotaHash(alvo){
  return '#/' + alvo.cat
       + (alvo.chave ? '/' + encodeURIComponent(String(alvo.chave)) : '')
       + (alvo.q ? '?q=' + encodeURIComponent(alvo.q) : '');
}

/* Escolhe entre empresa e usuario pela natureza. As duas sao linhas de `pessoa`
   e so o catalogo sabe qual das duas telas abrir. */
/* OACI e a chave natural do aerodromo: e o que o usuario le no relatorio e o
   que a API do tempo usa. `oaciDestino` so precisa existe para deixar claro
   que aqui o destino e por codigo, e nao por id. */
function oaciDestino(r){
  return r && r.icao ? {cat: 'aerodromo', chave: r.icao} : null;
}

function pessoaDestino(r){
  if (!r || r.pessoa_id == null) return null;
  return {cat: r.natureza === 'FISICA' ? 'usuario' : 'empresa', chave: r.pessoa_id};
}

/* Ancora de linha. Fica como <a> de verdade para que o botao do meio e o
   "abrir em nova aba" continuem funcionando, e para que sem JavaScript o
   destino ainda exista no HTML. */
function ancora(cat, chave, rotulo, q){
  if (cat == null || (chave == null && !q)) return esc(rotulo);
  return `<a class="reg" href="${esc(rotaHash({cat:cat, chave:chave, q:q}))}"`
       + ` data-cat="${esc(cat)}"`
       + (chave != null ? ` data-chave="${esc(String(chave))}"` : '')
       + (q ? ` data-q="${esc(q)}"` : '') + `>${esc(rotulo)}</a>`;
}

document.addEventListener('click', ev => {
  const a = ev.target.closest('a.reg, [data-cat]');
  if (!a) return;
  ev.preventDefault();
  abrir({cat: a.dataset.cat, chave: a.dataset.chave, q: a.dataset.q});
});


/* ---------------------------------------------------------------- KPIs */
function kpis(r){
  const k = [
    ['Aeronaves', nf(r.aeronaves), 'no snapshot', 'aeronave'],
    ['Empresas', nf(r.empresas), 'CNPJ', 'empresa'],
    ['Usuários', nf(r.usuarios), 'pessoa física', 'usuario'],
    ['Fabricantes', nf(r.fabricantes), 'empresa', 'fabricante'],
    ['Modelos', nf(r.modelos), '', 'modelo'],
    ['Marcas', nf(r.marcas), '', 'marca'],
    ['Aeródromos', nf(r.aerodromos), '', 'aerodromo'],
    ['Drones (SISANT)', nf(r.sisant), '', 'drone'],
    ['Vínculos', nf(r.participacoes), '12 meses', 'vinculo'],
  ];
  // O quarto item de cada linha e o catalogo de destino. Um KPI que nao abre
  // nada e so um numero; estes abrem a lista do registro correspondente.
  document.getElementById('kpis').innerHTML = k.map(
    ([t, v, s, cat]) => `<a class="kpi link" href="#/${cat}" data-cat="${cat}">`
      + `<b>${v}</b><span>${esc(t)}</span>${s ?
      `<div class="sub" style="font-size:11px">${esc(s)}</div>` : ''}</a>`).join('');
}

/* --------------------------------------------------------------- SVG */
const NS = 'http://www.w3.org/2000/svg';
function svg(w, h){
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', `0 0 ${w} ${h}`);
  s.setAttribute('width', '100%');
  s.setAttribute('height', h);
  s.setAttribute('preserveAspectRatio', 'xMinYMin meet');
  return s;
}
function el(t, a){
  const n = document.createElementNS(NS, t);
  for (const k in a) n.setAttribute(k, a[k]);
  return n;
}

/* Linha temporal da frota. */
function linhaTempo(dados, alvo){
  const W = 940, H = 260, L = 62, R = 18, T = 16, B = 42;
  const s = svg(W, H);
  const max = Math.max(...dados.map(d => d.contagem)) * 1.04;
  const min = Math.min(...dados.map(d => d.contagem)) * 0.985;
  const x = i => L + i * (W - L - R) / (dados.length - 1);
  const y = v => T + (H - T - B) * (1 - (v - min) / (max - min));

  for (let i = 0; i <= 4; i++){
    const v = min + (max - min) * i / 4;
    s.appendChild(el('line', {x1:L, y1:y(v), x2:W-R, y2:y(v),
      stroke:'#21262d', 'stroke-width':1}));
    const t = el('text', {x:L-9, y:y(v)+4, fill:'#8b949e', 'font-size':11,
      'text-anchor':'end'});
    t.textContent = nf(Math.round(v));
    s.appendChild(t);
  }

  const cor = d => d.esquema_era === 'A' ? '#58a6ff'
                   : d.esquema_era === 'B' ? '#f0883e' : '#3fb950';
  dados.forEach((d, i) => {
    s.appendChild(el('circle', {cx:x(i), cy:y(d.contagem), r:4.5,
      fill:cor(d), stroke:'#0d1117', 'stroke-width':1.5}));
    const t = el('text', {x:x(i), y:H-B+16, fill:'#8b949e', 'font-size':10.5,
      'text-anchor':'middle'});
    t.textContent = d.mes.slice(2);
    s.appendChild(t);
  });
  const path = dados.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.contagem)}`).join(' ');
  s.appendChild(el('path', {d:path, fill:'none', stroke:'#58a6ff',
    'stroke-width':2, opacity:.55}));

  const ini = dados[0], fim = dados[dados.length - 1];
  const alta = ((fim.contagem / ini.contagem - 1) * 100).toFixed(2);
  s.appendChild(el('line', {x1:x(0), y1:y(ini.contagem), x2:x(dados.length-1),
    y2:y(fim.contagem), stroke:'#58a6ff', 'stroke-width':1, 'stroke-dasharray':'3 3',
    opacity:.5}));
  alvo.appendChild(s);
  const dif = document.createElement('p');
  dif.className = 'note';
  dif.style.marginTop = '8px';
  dif.innerHTML = `${nf(ini.contagem)} → ${nf(fim.contagem)} aeronaves em
    ${dados.length} snapshots: <b style="color:var(--b)">+${alta}%</b>.`;
  alvo.appendChild(dif);
  const lg = document.createElement('div');
  lg.className = 'legend';
  lg.innerHTML = [['A · PROPRIETARIO','#58a6ff'],['B · PROPRIETARIOSARRAY','#f0883e'],
    ['C · PROPRIETARIOSJSON','#3fb950']]
    .map(([t, c]) => `<span><i style="background:${c}"></i>${t}</span>`).join('');
  alvo.appendChild(lg);
}

/* Barras horizontais (ranking). */
function barras(itens, alvo, campo, rotulo, nav){
  const W = 460, rowH = 25, L = 196, R = 66, T = 8;
  const s = svg(W, T + itens.length * rowH + 6);
  const max = Math.max(...itens.map(i => i[campo] || 0), 1);
  itens.forEach((d, i) => {
    const y = T + i * rowH;
    const w = Math.max(2, (d[campo] / max) * (W - L - R));
    const nome = String(d[rotulo] || '(desconhecido)');
    const curto = nome.length > 30 ? nome.slice(0, 29) + '…' : nome;
    const t1 = el('text', {x:L-8, y:y+14, fill:'#e6edf3', 'font-size':11.5,
      'text-anchor':'end'});
    t1.textContent = curto;
    s.appendChild(t1);
    if (nav){
      const destino = nav.destino(d);
      // Faixa invisivel sobre a linha inteira: o clique pega a barra e o
      // rotulo, e nao so os 11px do texto.
      if (destino){
        const alvo2 = el('rect', {x:0, y:y-2, width:W, height:rowH-4,
          fill:'transparent', style:'cursor:pointer'});
        const dica = el('title', {});
        dica.textContent = 'abrir ' + (CATALOGOS[destino.cat] || destino.cat)
                         + ': ' + nome;
        alvo2.appendChild(dica);
        alvo2.addEventListener('click', () => abrir(destino));
        s.appendChild(alvo2);
      }
    }
    s.appendChild(el('rect', {x:L, y:y+3, width:w, height:13, rx:2.5,
      fill:'#58a6ff', opacity:.82}));
    const t2 = el('text', {x:L+w+7, y:y+14, fill:'#8b949e', 'font-size':11});
    t2.textContent = nf(d[campo]);
    s.appendChild(t2);
  });
  alvo.appendChild(s);
}

/* Tabela simples. */
function tabela(cols, linhas, alvo, fmt, nav){
  const navCol = nav && cols.some(c => c.k === nav.k) ? nav.k : null;
  let h = '<table><thead><tr>' +
    cols.map(c => `<th class="${c.num ? 'num' : ''}">${esc(c.t)}</th>`).join('') +
    '</tr></thead><tbody>';
  for (const ln of linhas){
    h += '<tr>' + cols.map(c => {
      const v = ln[c.k];
      let rendered = (fmt && fmt[c.k]) ? fmt[c.k](v, ln) : esc(v);
      if (navCol && c.k === navCol && !(fmt && fmt[c.k])){
        const d = nav.destino(ln);
        if (d) rendered = ancora(d.cat, d.chave, v, d.q || nav.q || '');
      }
      return `<td class="${c.num ? 'num' : ''}">${rendered}</td>`;
    }).join('') + '</tr>';
  }
  alvo.innerHTML = h + '</tbody></table>';
}

/* ------------------------------------------------- concentração */

/* Gráfico de linhas com várias séries, para a série mensal do HHI.
   `series` é [{nome, cor, tracejado, pontos: [{mes, v}]}]. */
function linhas(series, alvo){
  const W = 520, H = 250, L = 46, R = 14, T = 14, B = 34;
  const s = svg(W, H);
  const todos = series.flatMap(x => x.pontos.map(p => p.v));
  const max = Math.max(...todos) * 1.08;
  const min = Math.min(...todos) * 0.90;
  const n = series[0].pontos.length;
  const x = i => L + i * (W - L - R) / (n - 1);
  const y = v => T + (H - T - B) * (1 - (v - min) / (max - min));

  for (let i = 0; i <= 4; i++){
    const v = min + (max - min) * i / 4;
    s.appendChild(el('line', {x1:L, y1:y(v), x2:W-R, y2:y(v),
      stroke:'#21262d', 'stroke-width':1}));
    const t = el('text', {x:L-7, y:y(v)+4, fill:'#8b949e', 'font-size':10.5,
      'text-anchor':'end'});
    t.textContent = dec(v, 1);
    s.appendChild(t);
  }
  series.forEach(sr => {
    const d = sr.pontos.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
    s.appendChild(el('path', {d, fill:'none', stroke:sr.cor, 'stroke-width':2,
      'stroke-dasharray': sr.tracejado ? '5 4' : 'none',
      'stroke-linejoin':'round'}));
    sr.pontos.forEach((p, i) => {
      if (i === 0 || i === n - 1){
        const c = el('circle', {cx:x(i), cy:y(p.v), r:3, fill:sr.cor});
        s.appendChild(c);
      }
    });
  });
  [0, Math.floor((n - 1) / 2), n - 1].forEach(i => {
    const t = el('text', {x:x(i), y:H - 10, fill:'#8b949e', 'font-size':10.5,
      'text-anchor': i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'});
    t.textContent = series[0].pontos[i].mes;
    s.appendChild(t);
  });
  alvo.appendChild(s);

  const leg = document.createElement('div');
  leg.className = 'legend';
  leg.innerHTML = series.map(sr =>
    `<span><i style="background:${sr.cor}"></i>${esc(sr.nome)}</span>`).join('');
  alvo.after(leg);
}

/* Curva de Lorenz contra a diagonal de perfeita igualdade. */
function lorenz(pontos, alvo){
  const W = 520, H = 250, P = 44;
  const s = svg(W, H);
  const x = v => P + v * (W - P - 12);
  const y = v => H - P - v * (H - P - 12);

  for (let i = 0; i <= 4; i++){
    const f = i / 4;
    s.appendChild(el('line', {x1:P, y1:y(f), x2:W-12, y2:y(f),
      stroke:'#21262d', 'stroke-width':1}));
    const t = el('text', {x:P-7, y:y(f)+4, fill:'#8b949e', 'font-size':10.5,
      'text-anchor':'end'});
    t.textContent = (f * 100).toFixed(0) + '%';
    s.appendChild(t);
  }
  s.appendChild(el('line', {x1:x(0), y1:y(0), x2:x(1), y2:y(1),
    stroke:'#484f58', 'stroke-width':1.4, 'stroke-dasharray':'5 4'}));
  const ta = el('text', {x:W - 16, y:y(1) - 6, fill:'#484f58', 'font-size':10.5,
    'text-anchor':'end'});
  ta.textContent = 'perfeita igualdade';
  s.appendChild(ta);

  const d = pontos.map((p, i) =>
    `${i ? 'L' : 'M'}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(' ');
  s.appendChild(el('path', {d, fill:'none', stroke:'#58a6ff',
    'stroke-width':2.2, 'stroke-linejoin':'round'}));

  /* Marca onde a curva atravessa metade da frota. */
  const meio = pontos.reduce((a, p) => p.y >= 0.5 ? (a ? a : p) : a, null);
  if (meio){
    s.appendChild(el('line', {x1:x(meio.x), y1:y(meio.y), x2:x(meio.x), y2:y(0),
      stroke:'#f0883e', 'stroke-width':1, 'stroke-dasharray':'3 3'}));
    s.appendChild(el('circle', {cx:x(meio.x), cy:y(meio.y), r:3.5,
      fill:'#f0883e'}));
    const t = el('text', {x:x(meio.x) + 7, y:y(meio.y) - 8, fill:'#f0883e',
      'font-size':10.5});
    t.textContent = (meio.x * 100).toFixed(0) + '% dos donos = metade da frota';
    s.appendChild(t);
  }
  const e = el('text', {x:P, y:H - 8, fill:'#8b949e', 'font-size':10.5});
  e.textContent = '→ proporção acumulada de donos';
  s.appendChild(e);
  alvo.appendChild(s);
}

/* `g` é o bloco de concentração: {analise, serie}. */
function concentracao(g){
  const ca = g.analise;
  const serie = g.serie;
  const i = ca.indices, ir = ca.indices_radicais, t = ca.titulares;

  const k = [
    ['HHI frota', dec(i.hhi_frota), '0–10.000 pts'],
    ['HHI crédito', dec(i.hhi_credito), 'sem govt e fabricante'],
    ['HHI declarado', dec(i.hhi_declarado),
      i.cobertura_pct + '% de cobertura'],
    ['Gini', dec(i.gini, 3), '0 = igual, 1 = monopólio'],
    ['CR4', dec(i.cr4) + '%', '4 maiores donos'],
    ['Maior dono', dec(i.top1) + '%', 'da frota'],
    ['Titularizadas', dec(ca.titularizacao.pct_frota, 1) + '%',
      'dono não opera'],
    ['Veículos', nf(t.grupos), 'só guardam título'],
  ];
  document.getElementById('conc-kpis').innerHTML = k.map(
    ([a, b, c]) => `<div class="kpi"><b>${esc(b)}</b><span>${esc(a)}</span>
      <div class="sub" style="font-size:11px">${esc(c)}</div></div>`).join('');

  linhas([
    {nome: 'HHI frota', cor: '#58a6ff', pontos: serie},
    {nome: 'HHI frota (radicais)', cor: '#a371f7', tracejado: true, pontos: serie},
    {nome: 'HHI de crédito', cor: '#f0883e', pontos: serie},
  ].map(s => ({...s, pontos: s.pontos.map(p => ({mes: p.mes, v: p[{
    'HHI frota': 'hhi_frota', 'HHI frota (radicais)': 'hhi_frota_radicais',
    'HHI de crédito': 'hhi_credito'}[s.nome]]}))})),
    document.getElementById('g-hhi-serie'));

  lorenz(i.curva_lorenz, document.getElementById('g-lorenz'));

  barras(ca.por_tipo.map(([k2, v]) => ({k2, v})),
    document.getElementById('g-segmento'), 'v', 'k2');

  tabela([{k:'pool', t:'Pool de securitização'},
          {k:'series', t:'Séries', num:true},
          {k:'aeronaves', t:'Aeronaves', num:true}], t.pools_top,
    document.getElementById('t-pools'), {
      series: v => nf(v),
      aeronaves: v => nf(v),
    });

  tabela([{k:'nome', t:'Veículo'}, {k:'uf', t:'UF'},
          {k:'aeronaves', t:'Aeronaves', num:true},
          {k:'pct_frota', t:'% frota', num:true},
          {k:'series', t:'Grafias', num:true}], t.top,
    document.getElementById('t-titulares'), {
      aeronaves: v => nf(v),
      pct_frota: v => dec(v) + '%',
      series: v => v > 1 ? nf(v) + ' grafias' : '—',
    }, {k:'nome', destino: pessoaDestino});

  /* `tabela` escreve no alvo e não devolve nada, então a lista de grafias
     é montada num div solto antes de ser anexada à nota. */
  const f = ca.fragmentacao;
  const grafias = document.createElement('div');
  tabela([{k:'grafias', t:'Grafias na fonte'},
          {k:'aeronaves', t:'Aeronaves', num:true}],
    f.exemplos, grafias, {
      // Cada grafia e um `pessoa.id` diferente — e exatamente ai que nasce a
      // fragmentacao. Cada uma abre o seu cadastro.
      grafias: (v, ln) => (ln.por_grafia || []).length
        ? ln.por_grafia.map(g => ancora('empresa', g.pessoa_id, g.grafia)).join(' · ')
        : esc((v || []).join(' · ')),
      aeronaves: v => nf(v),
    });
  document.getElementById('t-fragmentacao').innerHTML =
    `<p class="note" style="margin-bottom:11px"><b>${nf(f.grupos_com_varias_grafias)}</b>
     proprietários aparecem em mais de uma grafia, somando
     <b>${nf(f.aeronaves_afetadas)}</b> aeronaves. Funde-los pelo conjunto de
     radicais move o HHI de frota de ${dec(i.hhi_frota)} para
     ${dec(ir.hhi_frota)} e o de crédito de ${dec(i.hhi_credito)}
     para ${dec(ir.hhi_credito)}.</p>` + grafias.innerHTML;
}

/* --------------------------------------------------------- seções */
function secoes(g){
  document.getElementById('cabecalho').innerHTML =
    `${g.resumo.pessoas.toLocaleString('pt-BR')} pessoas · snapshot
     <b>${g.qualidade.mes}</b> · ${g.frota.length} snapshots mensais ·
     gerado de dados abertos da ANAC`;

  kpis(g.resumo);

  concentracao(g.concentracao);

  linhaTempo(g.frota, document.getElementById('g-frota'));

  const t2 = document.getElementById('t-prop');
  tabela([{k:'nome', t:'Proprietário'}, {k:'natureza', t:'Tipo'},
          {k:'uf', t:'UF'}, {k:'aeronaves', t:'Aeronaves', num:true},
          {k:'pct_medio', t:'Part. média', num:true}], g.propriedade, t2,
    {natureza: v => `<span class="tag ${v === 'FISICA' ? 't135' : 't121'}">${
      v === 'FISICA' ? 'Pessoa física' : 'Empresa'}</span>`},
    {k:'nome', destino: pessoaDestino});

  document.getElementById('hhi').innerHTML =
    `<b>${g.hhi.hhi}</b> &nbsp;<span style="color:var(--dim);font-size:12px">
     HHI · top 1 = ${pct(g.hhi.top1)} · ${nf(g.hhi.participantes)} proprietários
     distintos</span>`;

  const t4 = document.getElementById('t-auth');
  tabela([{k:'nome', t:'Operador'}, {k:'uf', t:'UF'},
          {k:'aeronaves', t:'Aeronaves', num:true},
          {k:'reg121', t:'121 reg.'},
          {k:'reg135', t:'135 reg.'},
          {k:'sae', t:'SAE'}], g.autorizacoes, t4,
    {reg121: v => v ? '<span class="tag t121">S</span>' :
        '<span class="tag tna">N</span>',
     reg135: v => v ? '<span class="tag t135">S</span>' :
        '<span class="tag tna">N</span>',
     sae: v => v ? '<span class="tag tna">S</span>' : '<span class="tag tna">N</span>'},
    {k:'nome', destino: pessoaDestino});

  barras(g.classes, document.getElementById('g-classe'), 'n', 'classe');
  barras(g.decadas, document.getElementById('g-decada'), 'n', 'decada');
  tabela([{k:'nome', t:'Fabricante'}, {k:'aeronaves', t:'Aeronaves', num:true},
          {k:'modelos', t:'Modelos', num:true}], g.fabricantes,
    document.getElementById('t-fab'),
    null, {k:'nome', destino: r => r.fabricante_id == null ? null
      : {cat: 'fabricante', chave: r.fabricante_id}});
  tabela([{k:'ds_modelo', t:'Modelo'}, {k:'marca', t:'Marca'},
          {k:'fabricante', t:'Fabricante'}, {k:'n', t:'Frota', num:true}],
    g.modelos, document.getElementById('t-modelos'),
    null, {k:'ds_modelo', destino: r => r.modelo_id == null ? null
      : {cat: 'modelo', chave: r.modelo_id}});

  barras(g.aero_uf, document.getElementById('g-aero'), 'n', 'uf');
  tabela([{k:'icao', t:'OACI'}, {k:'nome', t:'Nome'}, {k:'municipio', t:'Município'},
          {k:'uf', t:'UF'}, {k:'altitude', t:'Alt.'}, {k:'situacao', t:'Situação'}],
    g.aero_publicos, document.getElementById('t-aero'),
    null, {k:'icao', destino: r => r.icao ? {cat: 'aerodromo', chave: r.icao} : null});

  barras(g.sisant_ramo, document.getElementById('g-sisant'), 'n', 'ramo',
    {destino: d => (d.ramo && !d.ramo.startsWith('('))
      ? {cat: 'drone', q: d.ramo} : null});
  const split = g.sisant_split.reduce((a, r) => (a[r.natureza] = r.n, a), {});
  document.getElementById('sisant-split').innerHTML =
    `${nf(split.FISICA || 0)} pessoa física · ${nf(split.JURIDICA || 0)} empresa
     · <b>${nf(g.resumo.sisant)}</b> registros`;

  tabela([{k:'nome', t:'Entidade'}, {k:'papel', t:'Papel'},
          {k:'vinculos', t:'Vínculos', num:true},
          {k:'aeronaves', t:'Aeronaves', num:true}], g.rede,
    document.getElementById('t-rede'), null, {k:'nome', destino: pessoaDestino});

  const q = g.qualidade, t = q.participacoes;
  const pc = v => ((v / t) * 100).toFixed(1) + '%';
  document.getElementById('t-qual').innerHTML = `
    <table><thead><tr><th>Indicador</th><th class="num">Valor</th>
    <th class="num">% dos vínculos</th></tr></thead><tbody>
    <tr><td>Vínculos totais</td><td class="num">${nf(t)}</td><td class="num">100%</td></tr>
    <tr><td>Sem documento na fonte</td><td class="num">${nf(q.sem_documento)}</td>
        <td class="num">${pc(q.sem_documento)}</td></tr>
    <tr><td>Com CPF mascarado</td><td class="num">${nf(q.cpf_mascarado)}</td>
        <td class="num">${pc(q.cpf_mascarado)}</td></tr>
    <tr><td>Proprietário sem percentual</td><td class="num">${nf(q.proprietarios_sem_pct)}</td>
        <td class="num">${pc(q.proprietarios_sem_pct)}</td></tr>
    <tr><td>Aeronave sem fabricante</td><td class="num">${nf(q.aeronave_sem_fabricante)}</td>
        <td class="num">—</td></tr>
    <tr><td>Aeronave sem modelo</td><td class="num">${nf(q.aeronave_sem_modelo)}</td>
        <td class="num">—</td></tr>
    <tr><td>Pessoas sem nenhum vínculo</td><td class="num">${nf(q.pessoas_sem_vinculo)}</td>
        <td class="num">—</td></tr>
    <tr><td>Violações de FK</td><td class="num">${q.violacoes_fk}</td>
        <td class="num">—</td></tr>
    </tbody></table>`;

  const rd = g.redemet;
  document.getElementById('redemet-cabecalho').innerHTML = rd.localidades
    ? `<b>${nf(rd.localidades)}</b> localidades com status · ${nf(rd.vinculadas)}
       também presentes no cadastro da ANAC · ${nf(rd.metar)} METAR/SPECI ·
       ${nf(rd.taf)} TAF · ${nf(rd.requisicoes)} requisições na coleta`
    : 'Nenhuma coleta REDEMET registrada. Rode <span class="cite">etl/run.py --redemet</span>.';

  const CORES = {g: '#3fb950', gw: '#57a64a', y: '#d29922', yw: '#bb8009',
                 cinza: '#484f58'};
  barras(g.redemet_cores, document.getElementById('g-redemet'), 'n', 'cor');
  const leg = document.createElement('div');
  leg.className = 'legend';
  leg.innerHTML = Object.entries(CORES).map(([c, h]) =>
    `<span><i style="background:${h}"></i>${c}</span>`).join('');
  document.getElementById('g-redemet').after(leg);

  tabela([{k:'icao', t:'OACI'}, {k:'validade_inicial', t:'Validade'},
          {k:'mensagem', t:'Mensagem'}], g.redemet_metar,
    document.getElementById('t-metar'),
    null, {k:'icao', destino: oaciDestino});
  tabela([{k:'icao', t:'OACI'}, {k:'mensagem', t:'TAF'}], g.redemet_taf,
    document.getElementById('t-taf'),
    null, {k:'icao', destino: oaciDestino});

  document.getElementById('rodape').innerHTML =
    `Fonte: dados abertos da ANAC (RAB, SISANT, aeródromos, produtos, peças).
     Base normativa do bloco 121/135: <span class="cite">RBAC 45.12-I(a)</span>
     — a aeronave só opera sob o RBAC 135 se inscrita como TRANSPORTE PÚBLICO.
     Gerado localmente, sem CDN.`;
}

secoes(D);
"""
