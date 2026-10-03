"""Interface do registro: painel lateral, catálogos, detalhe e atualizações.

Um HTML só, sem CDN e sem build, servido por `registro_http.py`.

**O painel antigo não foi apagado.** `build/dashboard.html` continua inteiro e
aparece dentro de um `<iframe>` na rota *Painel*, com as mesmas seções e
gráficos. O que mudou é que ele deixou de ser uma folha isolada: cada número,
nome e linha dele aponta para um cadastro real, e o pedido de abertura sai do
iframe para o pai por `abrirDetalhe`. A navegação por fragmento
(`#/empresa/123`) faz o botão voltar do navegador funcionar, e faz o painel
aberto sozinho cair na mesma tela do app.

Três decisões que valem explicar:

**1. Vazio é o estado honesto.** Um campo que a ANAC não publica aparece como
"não consta na fonte", nunca preenchido por dedução. Um campo que a fonte
preenche aparece **travado**, com o selo *oficial ANAC*: quem altera dado
oficial é a ANAC. Editável é só o que a fonte não traz — e o servidor é quem
decide isso, e devolve `400` se a tela tentar gravar em campo travado.

**2. A tela nunca decide o que é editável.** Ela recebe `bloqueados` do servidor
e desenha. Se as duas disagreeassem, a tela prometeria uma edição que a API
recusaria — e o usuário perderia o dado digitado.

**3. A cor do ícone é derivada, não lembrada.** Vermelho é "o intervalo
venceu", amarelo é "rodando agora", verde é "dentro do intervalo". Como o
carimbo da última verificação fica no banco, a contagem sobrevive a servidor
desligado.
"""

HTML = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Registro ANAC · painel e cadastros</title>
<style>
:root{
  --bg:#0d1117; --card:#161b22; --line:#21262d; --txt:#e6edf3; --dim:#8b949e;
  --a:#58a6ff; --b:#3fb950; --c:#f0883e; --warn:#d29922; --erro:#f85149;
  --amarelo:#d29922;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
a{color:var(--a)}

/* ------------------------------------------------------------------ layout */
/* Três linhas por duas colunas, com a terceira linha inteira. O grid de duas
   colunas e duas linhas faz o mesmo que a tabela do enunciado; usar `grid` e
   não `<table>` porque o conteúdo não é tabular e a coluna da esquerda
   encolhe até sumir quando o usuário colapsa.
     linha 1: [logo · busca · colapsar] [usuário]
     linha 2: [menus]                    [páginas]
     linha 3: [rodapé, ocupando as duas colunas]
   A linha 2 é o miolo: menu à esquerda, páginas à direita, lado a lado, e o
   rodapé fecha a tela embaixo. A coluna da esquerda é fixa por track, senão o
   conteúdo largo esticaria o menu. */
.app{display:grid;
  grid-template-columns:var(--lateral, 330px) minmax(0,1fr);
  /* Linha 2 com `1fr` e não `auto`: as páginas é que ocupam o espaço
     restante, e é ela que rola (`main{overflow-y:auto}`). Com `auto`, a grade
     daria a linha a altura máxima do menu e o miolo ficaria torto. A linha 3
     é `auto` porque o rodapé tem altura própria e curta. */
  grid-template-rows:auto minmax(0,1fr) auto;
  grid-template-areas:"topo usuario" "menu conteudo" "rodape rodape";
  min-height:100vh}
.lateral{grid-area:topo}
.menu-area{grid-area:menu;overflow-y:auto}
.menu-area{overscroll-behavior:contain;min-height:0}
main{overflow-y:auto;min-height:0}
.app{height:100vh;overflow:hidden}
.usuario-area{grid-area:usuario}
main{grid-area:conteudo}
/* O rodapé é a faixa do status da atualização. Fica no fim da tela, abaixo do
   miolo, e não na linha 2 · coluna 2 — que é das páginas. */
.rodape{grid-area:rodape;display:flex;align-items:center;gap:14px;
  padding:9px 18px;border-top:1px solid var(--line);background:#0b0f14;
  min-width:0}
/* `#estado` é só o invólucro; o `.estado` de verdade é desenhado por
   `pintarEstado`. O `flex:1` é dele, para a contagem ficar encostada à
   direita e a marca do app no fim. */
.rodape #estado{flex:1 1 auto;min-width:0}
.rodape .marca-app{color:var(--dim);font-size:12px;white-space:nowrap}
.app.colapsada{--lateral:58px}

.topo{display:flex;align-items:center;gap:10px;padding:12px 16px;
  border-right:1px solid var(--line);border-bottom:1px solid var(--line);
  background:#0b0f14;min-width:0}
.logo{font-size:16px;font-weight:660;letter-spacing:-.2px;white-space:nowrap;
  flex:0 0 auto}
.icone{flex:0 0 auto;background:none;border:1px solid var(--line);color:var(--dim);
  border-radius:8px;width:32px;height:32px;cursor:pointer;font-size:14px;
  display:inline-flex;align-items:center;justify-content:center}
.icone:hover{color:var(--txt);border-color:var(--a)}
.icone.on{color:var(--a);border-color:var(--a)}
/* `min-width:0` no pai e `width:100%` no filho não bastam: o pai recebe o
   espaço que sobra (136px) e o input, por padrão, fica no seu `size` (210px),
   transbordando por cima da logo. O `flex-basis:0` + `width:100%` resolve os
   dois de uma vez — o pai passa a ocupar todo o espaço livre e o filho a
   acompanhar esse espaço, sem depender do `size` do input. */
.busca{position:relative;flex:1 1 0;min-width:0}
.busca input{width:100%;min-width:0;max-width:100%;box-sizing:border-box;
  padding:7px 11px 7px 30px;background:var(--card);border:1px solid var(--line);
  border-radius:8px;color:var(--txt);font-size:13px}
.busca input:focus{outline:none;border-color:var(--a)}
.busca .lupa{position:absolute;left:9px;top:50%;transform:translateY(-50%);
  color:var(--dim);font-size:13px;pointer-events:none}

.menu-area{border-right:1px solid var(--line);background:#0b0f14;
  padding:6px 0 24px}
.grupo{padding:14px 18px 5px;color:var(--dim);font-size:10.5px;
  text-transform:uppercase;letter-spacing:.6px;font-weight:600}
/* Ícone e texto juntos à esquerda, contagem encostada na direita. Com
   `space-between` e os três filhos, o espaço sobrando se dividia entre eles e
   o texto do meio acabava boiando no centro da linha. O `margin-left:auto` é
   do número, que é quem deve ir para a borda. */
.menu a{display:flex;justify-content:flex-start;align-items:center;gap:8px;
  padding:6px 18px;color:var(--txt);text-decoration:none;font-size:13.2px;
  border-left:2px solid transparent}
.menu a:hover{background:#161b22;border-left-color:var(--a)}
.menu a.on{background:#1a2331;border-left-color:var(--a);font-weight:600}
.menu .ic{flex:0 0 auto}
.menu a > span:not(.ic){min-width:0;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.menu .n{margin-left:auto;color:var(--dim);font-size:11px;
  font-variant-numeric:tabular-nums;flex:0 0 auto}
/* Colapsado: some o rótulo e sobram só os ícones. O menu continua clicável,
   e cada item tem `title` para o mouse parado dizer o que é. */
.app.colapsada .grupo,
.app.colapsada .menu a > span:not(.ic){display:none}
.app.colapsada .menu a{justify-content:center;padding:8px 0}
.app.colapsada .menu .n{display:none}
.app.colapsada .icone{margin:0 auto}
.app.colapsada .busca{display:none}
.app.colapsada .topo{flex-direction:column;gap:8px;padding:12px 8px}
/* Colapsado, o nome e o e-mail somem e a linha 1 · coluna 2 vira só o avatar. */
.app.colapsada #btn-usuario .txt{display:none}
.app.colapsada .logo{display:none}

.usuario-area{display:flex;align-items:center;gap:10px;
  padding:10px 18px;border-bottom:1px solid var(--line);background:#0b0f14;
  min-width:0}
.usuario-area{justify-content:flex-end}
#btn-usuario{display:flex;align-items:center;gap:10px;background:var(--card);
  border:1px solid var(--line);border-radius:9px;padding:6px 12px;
  color:var(--txt);cursor:pointer;font:inherit;text-align:left}
#btn-usuario:hover{border-color:var(--a)}
#btn-usuario .avatar{width:26px;height:26px;border-radius:50%;
  background:var(--a);color:#0d1117;display:inline-flex;align-items:center;
  justify-content:center;font-weight:700;font-size:12px}
#btn-usuario .txt b{display:block;font-size:12.8px;font-weight:600}
#btn-usuario .txt small{display:block;color:var(--dim);font-size:11px}
.estado{display:flex;align-items:center;gap:8px;width:100%;min-width:0}
.estado .txt{min-width:0}
.estado .txt b{display:block;font-size:12.8px;font-weight:600}
.estado .txt small{display:block;color:var(--dim);font-size:11px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.aba{display:flex;gap:6px;margin:0 0 14px}
.aba button{background:var(--card);border:1px solid var(--line);color:var(--txt);
  border-radius:999px;padding:6px 16px;cursor:pointer;font:inherit;font-size:12.8px}
.aba button:hover{border-color:var(--a)}
.aba button.on{background:#1a2331;border-color:var(--a);color:var(--txt);font-weight:600}

/* Sem `max-width`: a coluna 2 é a última da linha e a grade já entrega a
   largura inteira dela. O teto de 1420px deixava as tabelas presas numa faixa
   estreita com uma faixa vazia à direita, enquanto a linha 1 · coluna 1
   ocupa a coluna toda. `min-width:0` continua, para a tabela poder encolher
   e rolar dentro da coluna em vez de esticá-la. */
main{padding:20px 28px 70px;min-width:0}
h1{margin:0 0 4px;font-size:20px;font-weight:660;letter-spacing:-.2px}
h2{margin:0 0 4px;font-size:15.5px;font-weight:620}
h3{font-size:12px;font-weight:600;color:var(--dim);text-transform:uppercase;
  letter-spacing:.4px;margin:22px 0 9px}
.nota{color:var(--dim);font-size:12.5px;margin:0 0 14px;max-width:92ch}
.caixa{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:17px 19px;margin-bottom:15px}
.vazio{color:var(--dim);padding:22px 0;text-align:center;font-size:13px}

/* -------------------------------------------------------------------- KPIs */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(152px,1fr));
  gap:10px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;
  padding:12px 14px}
.kpi b{display:block;font-size:21px;font-weight:660;letter-spacing:-.4px}
.kpi span{color:var(--dim);font-size:11px;text-transform:uppercase;
  letter-spacing:.4px}

/* --------------------------------------------------------------- controle */
.barra{display:flex;gap:9px;margin-bottom:13px;flex-wrap:wrap;align-items:center}
input[type=search],input[type=text],input[type=number],select,textarea{
  background:#0d1117;border:1px solid var(--line);color:var(--txt);
  border-radius:6px;padding:7px 10px;font:inherit;font-size:13px;min-width:0}
input:focus,select:focus,textarea:focus{outline:2px solid var(--a);outline-offset:-1px}
/* `flex:1` sem `min-width`: o `min-width:210px` que estava aqui era menor que a
   coluna do menu (330px), e o input da busca transbordava por cima da logo em
   vez de encolher. Quem define o piso da busca agora é `.busca input`. */
input[type=search]{flex:1}
button{font:inherit;font-size:13px;border-radius:6px;padding:7px 13px;
  cursor:pointer;border:1px solid var(--line);background:#21262d;color:var(--txt)}
button:hover{border-color:var(--dim)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primario{background:var(--b);border-color:var(--b);color:#04121f;font-weight:600}
button.azul{background:var(--a);border-color:var(--a);color:#04121f;font-weight:600}
button.perigo{color:var(--erro);border-color:#6e2b2b}

table{width:100%;border-collapse:collapse;font-size:12.8px}
th{text-align:left;color:var(--dim);font-weight:560;padding:7px 9px;
  border-bottom:1px solid var(--line);font-size:11px;text-transform:uppercase;
  letter-spacing:.3px}
td{padding:6px 9px;border-bottom:1px solid #1c2128;vertical-align:middle}
tr.clicavel{cursor:pointer}
tr.clicavel:hover td{background:#1c2128}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}

/* ------------------------------------------------------------------ selos */
.selo{display:inline-block;padding:1px 7px;border-radius:11px;font-size:10.5px;
  font-weight:600;white-space:nowrap}
.s-anac{background:#1f6feb33;color:#58a6ff}
.s-edit{background:#2ea04333;color:#3fb950}
.s-vazio{background:#30363d;color:#8b949e}
.s-lock{background:#3d1d0d88;color:var(--warn)}
.s-erro{background:#6e2b2b55;color:#ff7b72}
.s-ok{background:#2ea04333;color:#3fb950}
.s-na{background:#30363d;color:#8b949e}
.aviso{background:#3d1d0d55;border:1px solid #9e6a03;border-radius:9px;
  padding:12px 15px;margin-bottom:16px;font-size:12.6px}
.aviso b{color:var(--warn)}

/* ---------------------------------------------------------------- campos */
.grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px}
.campo{display:flex;flex-direction:column;gap:4px}
.campo label{font-size:11.5px;color:var(--dim);display:flex;gap:6px;
  align-items:center;flex-wrap:wrap}
.campo input,.campo textarea{width:100%}
.campo.travado input{background:#0b0f14;color:var(--dim);border-style:dashed;
  cursor:not-allowed}
.campo.editando input{background:#0f1a12;border-color:#2ea04355}
.kv{display:grid;grid-template-columns:minmax(120px,auto) 1fr;gap:3px 14px;
  font-size:12.9px}
.kv dt{color:var(--dim)}
.kv dd{margin:0;font-variant-numeric:tabular-nums;word-break:break-word}
.grade2{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));
  gap:16px;align-items:start}
.msg{font-size:12.5px;min-height:18px;margin:0}
.msg.ok{color:var(--b)}
.msg.ruim{color:var(--erro)}
.pill{display:inline-block;background:#1c2128;border:1px solid var(--line);
  border-radius:11px;padding:1px 8px;font-size:11px;margin:2px 3px 2px 0;
  cursor:pointer;text-decoration:none;color:var(--txt)}
.pill:hover{border-color:var(--a)}
code,.cite{font-family:ui-monospace,Consolas,monospace;font-size:11.5px;
  color:var(--warn)}

/* ------------------------------------------------------------ atualização */
/* `.sema` é a caixa grande da tela de Atualizações. A do rodapé chama-se
   `.estado`, sem borda nem fundo, porque duas regras com o mesmo nome e
   propriedades diferentes se anulariam por ordem de Cascata. */
.sema{display:flex;align-items:center;gap:10px;padding:13px 15px;
  border:1px solid var(--line);border-radius:10px;background:var(--card);
  margin-bottom:15px}
.sema .luz{width:13px;height:13px;border-radius:50%;flex:0 0 auto;
  box-shadow:0 0 9px currentColor}
.luz.VERMELHO{background:#f85149;color:#f85149}
.luz.AMARELO{background:#d29922;color:#d29922}
.luz.VERDE{background:#3fb950;color:#3fb950}
.sema .txt{font-size:13px}
.sema .txt b{display:block;font-size:13.5px}
.sema .txt small{color:var(--dim);font-size:11.5px}
#conta{font-variant-numeric:tabular-nums;color:var(--dim);font-size:12.5px;
  margin-left:auto;text-align:right}
/* A altura vem da grade, não de `100vh`: a linha 3 já rola, e um `calc` com
   altura de janela ia deixar o painel mais alto que a área disponível, criando
   duas barras de rolagem encostadas. */
iframe{width:100%;height:min(74vh,1000px);min-height:520px;display:block;
  border:1px solid var(--line);border-radius:11px;background:#0d1117}
.diff td{font-size:12.4px}
.diff .antes{color:#ff7b72;text-decoration:line-through;opacity:.85}
.diff .depois{color:#7ee787}
pre.doc{background:#0b0f14;border:1px solid var(--line);border-radius:8px;
  padding:13px;overflow:auto;max-height:520px;font-size:12.3px;
  font-family:ui-monospace,Consolas,monospace;white-space:pre-wrap;
  word-break:break-word}
/* ------------------------------------------------- acesso (login e senha) */
.campo{display:block;margin:11px 0;font-size:12.5px;color:var(--dim)}
.campo input,.campo select{display:block;width:100%;margin-top:4px;padding:8px 11px;
  background:var(--card);border:1px solid var(--line);border-radius:8px;
  color:var(--txt);font:inherit}
.campo input:focus,.campo select:focus{outline:none;border-color:var(--a)}
.acesso{max-width:430px;margin:9vh auto 40px;padding:0 18px}
.acesso .caixa{padding:26px 28px}
.acesso h1{margin-bottom:6px}
.acesso .barra{margin-top:14px}
a.btn{display:inline-flex;align-items:center;padding:7px 13px;border-radius:8px;
  border:1px solid var(--line);text-decoration:none;color:var(--txt);font-size:13px}
a.btn:hover{border-color:var(--a)}
@media (max-width:900px){
  /* Empilhado, o grid tem cinco faixas, e não três: sem esta lista, a faixa do
     usuário caía no `minmax(0,1fr)` da lista de três linhas e zerava, deixando
     o nome e o e-mail sobrepostos ao menu. O `1fr` vai para as páginas. */
  .app{grid-template-columns:1fr;
    grid-template-rows:auto auto minmax(0,38vh) minmax(0,1fr) auto;
    grid-template-areas:"topo" "usuario" "menu" "conteudo" "rodape"}
  .topo,.menu-area,.usuario-area{border-right:none}
  main{padding:16px}
}
</style>
</head>
<body>
<div class="app" id="app">
  <!-- linha 1 · coluna 1 -->
  <header class="topo">
    <div class="logo">Registro ANAC</div>
    <div class="busca">
      <span class="lupa">&#128269;</span>
      <input type="search" id="busca-menu" placeholder="filtrar menus"
             autocomplete="off">
    </div>
    <button class="icone" id="colapsar" title="Colapsar o menu">&#10530;</button>
  </header>
  <!-- linha 1 · coluna 2 -->
  <div class="usuario-area">
    <button id="btn-usuario" title="Configurações da conta">
      <span class="avatar" id="usuario-inicial">?</span>
      <span class="txt"><b id="usuario-nome">—</b>
        <small id="usuario-email">—</small></span>
    </button>
  </div>
  <!-- linha 2 · coluna 1 -->
  <nav class="menu-area"><div id="menu"></div></nav>
  <!-- linha 2 · coluna 2: as páginas -->
  <main id="tela"></main>
  <!-- linha 3 · as duas colunas: o rodapé -->
  <footer class="rodape">
    <div id="estado"></div>
    <span class="marca-app">Registro ANAC</span>
  </footer>
</div>
<div id="acesso" style="display:none"></div>
<script>
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nf = n => n == null ? '—' : Number(n).toLocaleString('pt-BR');
const dec = (v, d = 2) => v == null || v === '' ? '—'
  : Number(v).toFixed(d).replace('.', ',');
const $$ = s => [...document.querySelectorAll(s)];

/* Os catálogos do menu, na ordem em que aparecem na barra lateral.

   `empresa` e `fabricante` continuam existindo como rota porque são usados em
   links antigos e no painel; o menu aponta para `organizacao`, que junta os
   dois em uma lista só com abas. Os ícones ficam aqui porque o menu colapsado
   mostra só eles. */
/* Todos os catálogos, com título e ícone. O quarto campo diz "não entra no
   menu de cadastros". "Vínculos" precisa continuar aqui porque a tela ainda
   existe (a rota, os títulos, as colunas e as puxadas de linha dependem
   dele); o que sai é o botão do menu, que foi para Configurações. Tirar a
   linha inteira deixaria a página sem título quando aberta por lá. */
const CATALOGOS = [
  ['organizacao', 'Empresas e fabricantes', '&#127970;'],
  ['usuario',     'Usuários',               '&#128101;'],
  ['aeronave',    'Matrículas',             '&#9992;'],
  ['modelo',      'Modelos',                '&#128204;'],
  ['marca',       'Marcas',                 '&#127991;'],
  ['aerodromo',   'Aeródromos',             '&#127757;'],
  ['drone',       'Drones (SISANT)',        '&#128039;'],
  ['vinculo',     'Vínculos',               '&#128279;', 'fora'],
];
const TITULOS = Object.fromEntries(CATALOGOS.map(c => [c[0], c[1]]));

async function api(caminho, opcoes) {
  const r = await fetch(caminho, Object.assign({credentials: 'same-origin'}, opcoes));
  let dados = {};
  try { dados = await r.json(); } catch (e) { /* resposta sem corpo */ }
  if (!r.ok) {
    const erro = new Error(dados.erro || ('HTTP ' + r.status));
    erro.status = r.status;
    // 401 em qualquer rota significa que a sessão acabou: derruba para o login
    // em vez de deixar a tela mostrar erro de dado.
    if (r.status === 401 && !caminho.includes('/sessao')) {
      ESTADO.usuario = null;
      telaLogin();
    }
    throw erro;
  }
  return dados;
}
async function put(caminho, corpo) {
  // Sem `X-Operador`: o nome do operador vem da sessão, no servidor. Mandar
  // pelo navegador deixaria o log de auditoria aceitar qualquer nome.
  return api(caminho, {method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(corpo)});
}

/* ------------------------------------------------------------- navegação */
const ROTA = {cat: null, chave: null, q: '', tela: 'painel', cfg: 'atualizacoes',
              aba: ''};

function rotaPara(rot) {
  if (rot.tela === 'painel') return '#/painel';
  if (rot.tela === 'config') return '#/configuracoes/' + (rot.cfg || 'atualizacoes');
  if (rot.tela === 'redefinir') return '#/redefinir';
  let alvo = '#/' + rot.cat
    + (rot.chave ? '/' + encodeURIComponent(rot.chave) : '')
    + (rot.aba ? '?aba=' + encodeURIComponent(rot.aba) : '');
  if (rot.q) alvo += (alvo.includes('?') ? '&' : '?') + 'q=' + encodeURIComponent(rot.q);
  return alvo;
}

function ir(rot) {
  const destino = rotaPara(Object.assign({}, ROTA, rot));
  if (location.hash === destino) render();
  else location.hash = destino;
}

/* Ponto de entrada chamado pelo painel dentro do iframe. Precisa estar no
   `window` porque o iframe acessa o pai por nome, não por import. */
window.abrirDetalhe = function (alvo) {
  ir({tela: alvo.cat ? 'catalogo' : 'painel', cat: alvo.cat || null,
      chave: alvo.chave || null, q: alvo.q || '', aba: ''});
};

const TELAS_CONFIG = ['atualizacoes', 'vinculos', 'senha', 'conta', 'usuarios'];

function lerHash() {
  const bruto = location.hash.replace(/^#\/?/, '');
  const [caminho, consulta] = bruto.split('?');
  const partes = caminho.split('/').filter(Boolean).map(decodeURIComponent);
  const p = new URLSearchParams(consulta || '');
  if (!partes.length) return {tela: 'painel'};
  if (partes[0] === 'redefinir') return {tela: 'redefinir'};
  if (partes[0] === 'configuracoes') {
    const cfg = TELAS_CONFIG.includes(partes[1]) ? partes[1] : 'atualizacoes';
    return {tela: 'config', cfg: cfg};
  }
  const cat = partes[0];
  if (!TITULOS[cat]) return {tela: 'painel'};
  return {tela: 'catalogo', cat: cat, chave: partes[1] || null,
          q: p.get('q') || '', aba: p.get('aba') || ''};
}

async function render() {
  Object.assign(ROTA, lerHash());
  if (ESTADO.sessaoResolvida && !ESTADO.usuario) return telaLogin();
  pintarMenu();
  if (ROTA.tela === 'painel') return telaPainel();
  if (ROTA.tela === 'config') return telaConfig();
  if (ROTA.tela === 'redefinir') return telaRedefinir();
  if (ROTA.chave) return telaDetalhe();
  return telaLista();
}

/* -------------------------------------------------------------------- menu */
const ESTADO = {contagens: {}, atualizacao: null, editando: false, pagina: 0,
                usuario: null, filtroMenu: '', aba: '', colapsado: false,
                catTela: ''};

/* "Vínculos" saiu dos cadastros: são 865 mil linhas de uma tabela de junção
   entre aeronave e pessoa, sem identidade própria — o mesmo dado já aparece
   como "Proprietários e operadores" no detalhe da aeronave e no da pessoa.
   Fica aqui, em Configurações, porque é o único jeito de consultar no
   sentido inverso (quem opera o quê) e por mês. */
const CONFIG_MENU = [
  ['#/configuracoes/atualizacoes', 'Atualizações', '&#128337;', 'atualizacoes'],
  ['#/configuracoes/vinculos',     'Vínculos',     '&#128279;', 'vinculos'],
  ['#/configuracoes/senha',        'Trocar senha', '&#128273;', 'senha'],
  ['#/configuracoes/conta',        'Minha conta',  '&#128100;', 'conta'],
  ['#/configuracoes/usuarios',     'Usuários',     '&#128101;', 'usuarios'],
];

function pintarMenu() {
  const termo = ESTADO.filtroMenu.trim().toLowerCase();
  const casa = t => !termo || t.toLowerCase().includes(termo);
  const naPainel = ROTA.tela === 'painel';
  let h = casa('Painel')
    ? '<div class="grupo">Painel</div><div class="menu">'
      + `<a href="#/painel" class="${naPainel ? 'on' : ''}" title="Painel">`
      + `<span class="ic">&#128202;</span><span>Painel</span></a></div>` : '';

  const cadastros = CATALOGOS.filter(c => c[3] !== 'fora' && casa(c[1]));
  if (cadastros.length) {
    h += '<div class="grupo">Cadastros</div><div class="menu">';
    for (const [cat, titulo, icone] of cadastros) {
      const on = ROTA.tela === 'catalogo' && ROTA.cat === cat;
      const n = ESTADO.contagens[cat];
      h += `<a href="#/${cat}" class="${on ? 'on' : ''}" title="${esc(titulo)}">`
         + `<span class="ic">${icone}</span><span>${esc(titulo)}</span>`
         + `<span class="n">${n != null ? nf(n) : ''}</span></a>`;
    }
    h += '</div>';
  }

  // "Usuários" é de administrador: esconder para quem não é evita um clique
  // que só ia dar 403.
  const configs = CONFIG_MENU.filter(([_, t]) => casa(t)
    && (ESTADO.usuario && ESTADO.usuario.papel === 'administrador'
        || !t.startsWith('Usuários')));
  if (configs.length) {
    h += '<div class="grupo">Configurações</div><div class="menu">';
    for (const [href, titulo, icone, chave] of configs) {
      const on = ROTA.tela === 'config' && ROTA.cfg === chave;
      h += `<a href="${href}" class="${on ? 'on' : ''}" title="${esc(titulo)}">`
         + `<span class="ic">${icone}</span><span>${esc(titulo)}</span></a>`;
    }
    h += '</div>';
  }
  if (!h) h = '<div class="vazio">nenhum menu corresponde ao filtro</div>';
  $('#menu').innerHTML = h;

  pintarEstado();
  pintarUsuario();
}

function pintarEstado() {
  const s = ESTADO.atualizacao;
  const cor = s ? s.cor : 'VERMELHO';
  const icone = cor === 'VERDE' ? '&#128994;' : cor === 'AMARELO' ? '&#128994;' : '&#128308;';
  $('#estado').innerHTML =
    `<div class="estado"><span class="luz ${cor}" title="${esc(cor)}">${icone}</span>`
    + `<span class="txt"><b>Atualização: ${esc(s ? s.rotulo : 'desconhecido')}</b>`
    + `<small>${esc(s ? (s.etapa ? s.etapa + ' · ' : '') + (s.mensagem || '') : 'verificando…')}</small></span>`
    + `<span id="conta">${esc(contagem(s))}</span></div>`;
}

function pintarUsuario() {
  const u = ESTADO.usuario;
  if (!u) {
    $('#usuario-nome').textContent = 'sem sessão';
    $('#usuario-email').textContent = 'entre para editar';
    $('#usuario-inicial').textContent = '?';
    return;
  }
  $('#usuario-nome').textContent = u.nome_completo || '(sem nome)';
  $('#usuario-email').textContent = u.email;
  $('#usuario-inicial').textContent =
    (u.nome_completo || u.email || '?').trim().charAt(0).toUpperCase();
}

/* ------------------------------------------------------------------ painel */
function telaPainel() {
  $('#tela').innerHTML = `
    <h1>Painel</h1>
    <p class="nota">O painel analítico completo, com todas as seções e gráficos.
    Cada número, nome e linha é clicável e abre o cadastro correspondente.
    As tabelas continuam exatamente como eram.</p>
    <iframe id="quadro" src="/painel" title="Painel do registro aeronáutico"></iframe>`;
}

/* ------------------------------------------------------------------- lista */
/* A ordem das cinco colunas principais de aeronave é sua: Matrículas,
   Operadores, Fabricante, Modelo e Número de série. A de vínculos é a mesma,
   com a pessoa no lugar dos operadores, porque é quem o vínculo liga. */
const COLUNAS = {
  organizacao:[['nome_exibido', 'Nome'], ['tipo', 'Tipo'], ['documento', 'CNPJ'],
               ['uf', 'UF'], ['org_nabrev', 'Abrev.'],
               ['n_modelos', 'Modelos', true], ['n_vinculos', 'Vínculos', true],
               ['n_socios', 'Sócios', true]],
  empresa:   [['razao_social', 'Nome'], ['nome', 'Nome na fonte'], ['documento', 'CNPJ/CPF'],
              ['uf', 'UF'], ['n_vinculos', 'Vínculos', true], ['n_socios', 'Sócios', true]],
  usuario:   [['nome', 'Nome'], ['documento', 'CPF'], ['uf', 'UF'],
              ['n_vinculos', 'Vínculos', true], ['n_drones', 'Drones', true]],
  aeronave:  [['matricula', 'Matrículas'], ['n_operadores', 'Operadores', true],
              ['fabricante', 'Fabricante'], ['modelo', 'Modelo'],
              ['numero_serie', 'Número de série'], ['marca', 'Marca (prefixo)'],
              ['ano', 'Ano'], ['cd_classe', 'Classe'], ['snapshot_mes', 'Mês'],
              ['n_vinculos', 'Vínculos', true]],
  fabricante:[['nome', 'Nome'], ['org_nabrev', 'Abrev.'], ['documento', 'CNPJ'],
              ['n_modelos', 'Modelos', true], ['n_aeronaves', 'Aeronaves', true]],
  modelo:    [['modelo', 'Modelo'], ['marca', 'Marca'], ['fabricante', 'Fabricante'],
              ['n_aeronaves', 'Aeronaves', true]],
  marca:     [['nome', 'Marca'], ['n_modelos', 'Modelos', true],
              ['n_aeronaves', 'Aeronaves', true], ['n_fabricantes', 'Fabricantes', true]],
  aerodromo: [['icao', 'OACI'], ['nome', 'Nome'], ['municipio', 'Município'], ['uf', 'UF'],
              ['tipo', 'Tipo'], ['situacao', 'Situação'], ['redemet_cor', 'REDEMET']],
  drone:     [['codigo_aeronave', 'Código'], ['modelo_nome', 'Modelo'],
              ['fabricante_nome', 'Fabricante'], ['responsavel', 'Responsável'],
              ['data_validade', 'Validade'], ['peso_max_kg', 'Peso (kg)', true]],
  vinculo:   [['matricula', 'Matrículas'], ['pessoa_nome', 'Pessoa'], ['papel', 'Papel'],
              ['numero_serie', 'Número de série'], ['modelo', 'Modelo'],
              ['percentual', '%', true], ['snapshot_mes', 'Mês'], ['operacao_121', '121'],
              ['operacao_135', '135']],
};

async function telaLista(catForcado) {
  const cat = catForcado || ROTA.cat;
  $('#tela').innerHTML = `
    <h1>${esc(TITULOS[cat])}</h1>
    <p class="nota">${esc(NOTAS[cat] || '')}</p>
    <div id="abas"></div>
    <div class="barra">
      <input type="search" id="q" placeholder="${esc(DICA[cat] || '')}" value="${esc(ROTA.q)}">
      <button id="buscar">Buscar</button>
      <button id="limpar">Limpar</button>
    </div>
    <div class="caixa">
      <div id="lista"><p class="vazio">carregando&hellip;</p></div>
      <div class="barra" style="margin:13px 0 0">
        <button id="mais">Carregar mais</button>
        <small id="contagem"></small>
      </div>
    </div>`;

  ESTADO.pagina = 0;
  ESTADO.itens = [];
  ESTADO.catTela = cat;
  const busca = $('#q');
  // Filtra enquanto se digita. O `debounce` evita uma consulta por tecla: sem
  // ele, "PR-BEL" dispararia seis pedidos ao banco para a mesma palavra.
  let relogio = null;
  const filtra = () => {
    clearTimeout(relogio);
    relogio = setTimeout(() => ir({q: busca.value.trim()}), 320);
  };
  busca.addEventListener('input', filtra);
  busca.addEventListener('keydown', e => {
    if (e.key === 'Enter') { clearTimeout(relogio); ir({q: busca.value.trim()}); }
  });
  $('#buscar').addEventListener('click', () => { clearTimeout(relogio); ir({q: busca.value.trim()}); });
  $('#limpar').addEventListener('click', () => { clearTimeout(relogio); ir({q: ''}); });
  $('#mais').addEventListener('click', () => carregarLista(true));
  await carregarLista(false);
}

async function carregarLista(mais) {
  const cat = ESTADO.catTela || ROTA.cat;
  if (!mais) { ESTADO.itens = []; ESTADO.pagina = 0; }
  const alvo = $('#lista');
  alvo.innerHTML = '<p class="vazio">carregando&hellip;</p>';
  const params = new URLSearchParams({
    limite: '60', offset: String(ESTADO.pagina), q: ROTA.q || ''});
  if (ROTA.aba) params.set('tipo', ROTA.aba);
  let d;
  try {
    d = await api(`/api/catalogo/${cat}?${params}`);
  } catch (e) {
    alvo.innerHTML = `<p class="vazio" style="color:var(--erro)">${esc(e.message)}</p>`;
    return;
  }
  ESTADO.itens = ESTADO.itens.concat(d.itens);
  ESTADO.pagina += d.itens.length;
  desenharAbas(d.abas || []);
  desenharLista();
  $('#contagem').textContent = `${nf(ESTADO.itens.length)} de ${nf(d.total)}`;
  $('#mais').style.display = d.proximo ? '' : 'none';
}

/* As abas vêm prontas na resposta (`d.abas`), com o valor do filtro e se está
   ativa. Desenhar aqui evita manter a lista de filtros duplicada entre a tela
   e o servidor — e foi exatamente essa duplicação que já causou um filtro
   rejeitado por nome diferente dos dois lados. */
function desenharAbas(abas) {
  const caixa = $('#abas');
  if (!caixa) return;
  if (!abas.length) { caixa.innerHTML = ''; return; }
  caixa.innerHTML = '<div class="aba">' + abas.map(a =>
    `<button class="${a.ativa ? 'on' : ''}" data-aba="${esc(a.chave)}">${esc(a.titulo)}</button>`
  ).join('') + '</div>';
  for (const b of $$('#abas button')) {
    b.addEventListener('click', () => ir({aba: b.dataset.aba, q: ''}));
  }
}

function desenharLista() {
  const alvo = $('#lista');
  if (!ESTADO.itens.length) {
    alvo.innerHTML = `<p class="vazio">Nada encontrado${ROTA.q
      ? ' para ' + esc(ROTA.q) : ''}.</p>`;
    return;
  }
  /* O catálogo vem de `ESTADO.catTela`, e não de `ROTA.cat`: a lista de
     vínculos é aberta de Configurações, rota que não tem `cat`. E a puxada
     da linha vai sempre pela rota de catálogo, para funcionar dos dois lados. */
  const cat = ESTADO.catTela || ROTA.cat;
  const cols = COLUNAS[cat];
  let h = '<table><thead><tr>'
    + cols.map(c => `<th class="${c[2] ? 'num' : ''}">${esc(c[1])}</th>`).join('')
    + '</tr></thead><tbody>';
  for (const item of ESTADO.itens) {
    h += '<tr class="clicavel" data-chave="' + esc(item.nav) + '">'
      + cols.map(c => {
          const v = item[c[0]];
          const texto = (v == null || v === '')
            ? '<span class="selo s-vazio">—</span>' : esc(v);
          return `<td class="${c[2] ? 'num' : ''}">${c[2] ? nf(v) : texto}</td>`;
        }).join('')
      + '</tr>';
  }
  alvo.innerHTML = h + '</tbody></table>';
  $$('#lista tr.clicavel').forEach(tr => tr.addEventListener('click',
    () => ir({tela: 'catalogo', cat: cat, chave: tr.dataset.chave})));
}

/* ------------------------------------------------------------------ detalhe */
async function telaDetalhe() {
  $('#tela').innerHTML = '<p class="vazio">carregando cadastro&hellip;</p>';
  let d;
  try {
    d = await api(`/api/catalogo/${ROTA.cat}/${encodeURIComponent(ROTA.chave)}`);
  } catch (e) {
    $('#tela').innerHTML = `<h1>Cadastro não encontrado</h1>
      <p class="vazio" style="color:var(--erro)">${esc(e.message)}</p>`;
    return;
  }
  ESTADO.registro = d;
  ESTADO.editando = false;
  $('#tela').innerHTML = `
    <div class="barra">
      <button id="voltar">← ${esc(TITULOS[ROTA.cat])}</button>
      <span style="flex:1"></span>
      <small class="nota" style="margin:0">as alterações são gravadas com
        <b>${esc(ESTADO.usuario ? ESTADO.usuario.nome_completo : '')}</b></small>
    </div>
    <h1>${esc(tituloDetalhe(d))}</h1>
    <p class="msg" id="msg"></p>
    <div id="corpo"></div>`;
  $('#voltar').addEventListener('click', () => ir({chave: null}));
  $('#corpo').innerHTML = montarDetalhe(d);
  ligarDetalhe(d);
}

/* O título de cada registro: cada entidade tem um nome próprio, e mostrar
   `id 45051` no lugar do nome seria despejar o banco na tela. */
function tituloDetalhe(d) {
  switch (ROTA.cat) {
    case 'empresa': case 'usuario': case 'fabricante': case 'organizacao':
      return d.razao_social || d.nome_fantasia || d.nome || ('#' + (d.id || d.key));
    // Matrícula e marca são a mesma informação na fonte do RAB: o certificado
    // é a matrícula e o prefixo é a marca. Quando falta o certificado, a
    // identificação é montada com prefixo e série — por isso `matricula_exibida`.
    case 'aeronave':
      return d.matricula_ed || d.matricula_exibida || d.identidade
             || ('#' + (d.id || d.key));
    case 'aerodromo': return `${d.icao} · ${d.nome_ed || d.nome || ''}`;
    case 'modelo': return `${d.ds_modelo || ''} · ${d.marca || ''}`.trim();
    case 'marca': return d.nome;
    case 'drone': return d.codigo_aeronave;
    case 'vinculo':
      // Sem template: quando a fonte não traz matrícula, `${null}` viraria a
      // palavra "null" no título. Fica o que existe, e na falta de tudo, o id.
      return [d.matricula, d.pessoa_nome].filter(Boolean).join(' · ')
             || ('#' + (d.id || d.key));
    default: return d.nome || d.icao || '';
  }
}

/* Rótulos legíveis para o bloco "todos os campos da fonte". Vazio é o estado
   honesto: a tela escreve "não consta na fonte" e nunca deixa em branco. */
const ROTULOS = {
  id: 'Identificador no banco', natureza: 'Natureza', chave: 'Chave de identidade',
  nome: 'Nome na fonte', nome_fonte: 'Nome original da ANAC', nome_ed: 'Nome (com correção)',
  uf: 'UF', uf_fonte: 'UF original', uf_ed: 'UF (com correção)',
  documento: 'Documento', documento_bruto: 'Documento como veio na fonte',
  documento_mascarado: 'CPF mascarado na fonte', documento_invalido: 'Documento não identificável',
  razao_social: 'Razão social', nome_fantasia: 'Nome fantasia',
  justificativa: 'Justificativa da correção',
  site: 'Site', email: 'E-mail', telefone: 'Telefone', logo_url: 'Logo (URL)',
  end_logradouro: 'Logradouro', end_numero: 'Número', end_complemento: 'Complemento',
  end_bairro: 'Bairro', end_cep: 'CEP', end_municipio: 'Município', end_uf: 'UF do endereço',
  end_pais: 'País', origem_societario: 'Origem dos dados societários',
  origem_contato: 'Origem dos dados de contato', fonte_ref: 'Arquivo de origem',
  editado_em: 'Editado em', editado_por: 'Editado por',
  ciad: 'CIAD', icao: 'OACI', municipio: 'Município', tipo: 'Tipo', lat: 'Latitude',
  lon: 'Longitude', latitude: 'Latitude', longitude: 'Longitude', altitude: 'Altitude',
  municipio_servido: 'Município servido', uf_servido: 'UF servida',
  operacao_diurna: 'Operação diurna', operacao_noturna: 'Operação noturna',
  situacao: 'Situação', validade_registro: 'Validade do registro',
  nr_cert_matricula: 'Matrícula', nr_serie: 'Número de série',
  nr_ano_fabricacao: 'Ano de fabricação', cd_classe: 'Classe',
  cd_tipo_icao: 'Tipo de operação ICAO', ds_motivo_cancelamento: 'Motivo do cancelamento',
  nr_tripulacao_min: 'Tripulação mínima', nr_passageiros_max: 'Passageiros (máx.)',
  nr_assentos: 'Assentos', dt_validade_cva: 'Validade do CVA',
  dt_validade_ca: 'Validade do CA', dt_cancelamento: 'Cancelamento',
  cd_interdicao: 'Interdição', ds_gravame: 'Gravame', dt_matricula: 'Data de matrícula',
  tp_motor: 'Motor', qt_motor: 'Qtd. de motores', tp_pouso: 'Tipo de pouso',
  tp_ca: 'Tipo de CA', cd_proposito_cave: 'Propósito do CAVE',
  cf_operacional: 'Configuração operacional',
  ds_categoria_homologacao: 'Categoria de homologação',
  tp_operacao: 'Tipo de operação', dt_venda: 'Data de venda',
  ds_moeda: 'Moeda', nr_preco_venda: 'Preço de venda', marca: 'Marca (prefixo)',
  snapshot_mes: 'Snapshot', tp_uso: 'Tipo de uso',
  data_validade: 'Validade', fabricante_nome: 'Fabricante', modelo_nome: 'Modelo',
  num_serie: 'Número de série', peso_max_kg: 'Peso máximo (kg)',
  ramo_atividade: 'Ramo de atividade', responsavel: 'Responsável',
  papel: 'Papel', percentual: 'Percentual',
};
const ROTULO = k => ROTULOS[k] || k.replace(/_/g, ' ');
const booleano = v => v == null ? '—'
  : (v === 1 || v === '1' || v === 'S' || v === true ? 'S' : 'N');

/* Um par rótulo/valor com a procedência explícita. Campo da ANAC vem com o
   selo *oficial*; campo que não consta vem com *não consta*; campo digitado
   vem com *editado*. Nunca aparece valor sem um dos três. */
function par(rotulo, valor, selo) {
  return `<dt>${esc(rotulo)}</dt><dd>${valor == null || valor === ''
    ? '<span class="selo s-vazio">não consta na fonte</span>'
    : esc(valor) + (selo ? ' ' + selo : '')}</dd>`;
}
const seloAnac = '<span class="selo s-anac">oficial ANAC</span>';
const seloEditado = '<span class="selo s-edit">editado</span>';

/* Lista de campos da fonte, pulando o que já aparece em outro bloco. */
function blocoFonte(reg, excluir) {
  const skip = new Set(excluir || []);
  const chaves = Object.keys(reg).filter(k =>
    !skip.has(k) && !Array.isArray(reg[k]) && !k.startsWith('_')
    && !['id', 'key', 'chave', 'bloqueados', 'editaveis', 'auditoria',
        'overrides', 'sobreposicoes', 'totais', 'extra', 'regra',
        'aviso_cpf', 'mensagens_por_tipo'].includes(k));
  if (!chaves.length) return '';
  return `<div class="caixa"><h3>Todos os campos da fonte</h3>
    <dl class="kv">${chaves.map(k => par(ROTULO(k), reg[k], seloAnac)).join('')}</dl></div>`;
}

function tabela(cols, itens, nav) {
  if (!itens || !itens.length) return '';
  let h = '<table><thead><tr>'
    + cols.map(c => `<th class="${c[2] ? 'num' : ''}">${esc(c[1])}</th>`).join('')
    + '</tr></thead><tbody>';
  for (const it of itens) {
    const celulas = cols.map(c => {
      const v = c[3] ? c[3](v0(it, c[0])) : v0(it, c[0]);
      const txt = (v == null || v === '')
        ? '<span class="selo s-vazio">—</span>' : esc(v);
      return `<td class="${c[2] ? 'num' : ''}">${c[2] && v != null ? nf(v) : txt}</td>`;
    }).join('');
    if (!nav) { h += `<tr>${celulas}</tr>`; continue; }
    const chave = it.key != null ? it.key : it.id;
    h += `<tr class="clicavel" data-ir="${esc(chave)}" data-cat="${esc(nav.cat || ROTA.cat)}">`
       + `${celulas}</tr>`;
  }
  return h + '</tbody></table>';
}
function v0(item, chave) { return item[chave]; }

/* `data-ir` leva a chave e `data-cat` leva o catálogo: uma linha pode puxar
   para outro cadastro (um vínculo puxa a aeronave, uma empresa puxa a pessoa
   física) sem que o renderizador saiba qual é. */
function ligarPuxadas(raiz) {
  $$(`${raiz} [data-ir]`).forEach(tr => tr.addEventListener('click', () => {
    const cat = tr.dataset.cat || ROTA.cat;
    if (cat === ROTA.cat && tr.dataset.ir === String(ROTA.chave)) {
      ir({chave: null}); return;
    }
    ir({tela: 'catalogo', cat: cat, chave: tr.dataset.ir});
  }));
}

/* ------------------------------------------------------------ campo editável */
function entrada(nome, rotulo, valor, bloqueado, dica) {
  const lock = bloqueado
    ? `<span class="selo s-lock" title="Valor oficial dos dados abertos da ANAC">oficial ANAC</span>`
    : `<span class="selo s-edit">livre</span>`;
  return `<div class="campo ${bloqueado ? 'travado' : ''}">
    <label>${esc(rotulo)} ${lock}${dica ? ' <small>' + esc(dica) + '</small>' : ''}</label>
    <input type="text" name="${esc(nome)}" value="${esc(valor || '')}"
      ${bloqueado ? 'readonly disabled' : ''}></div>`;
}

/* O botão "Editar" só habilita o que o servidor liberou. Os travados ficam
   visíveis e desabilitados — o operador vê que o campo existe e que ele é da
   ANAC, em vez de simplesmente não encontrar o campo. */
function blocoEdicao(reg, campos) {
  const bloq = new Set(reg.bloqueados || []);
  return `<div class="caixa" id="caixa-edicao" hidden>
    <h3>Campos que a fonte da ANAC não traz</h3>
    <p class="nota">Estes campos não existem em nenhum arquivo de dados abertos da
    ANAC para este registro, então são os únicos que aceitam digitação. Os que a
    ANAC publica ficam travados acima: alterá-los é função da ANAC, e o servidor
    recusa a gravação com <code>400</code>.</p>
    <div class="grade">${campos.map(([nome, rotulo, valor]) =>
      entrada(nome, rotulo, valor, bloq.has(nome))).join('')}</div>
    <div class="barra" style="margin-top:14px">
      <button class="primario" id="salvar">Salvar alterações</button>
      <button id="cancelar">Cancelar</button>
    </div>
  </div>`;
}

/* Cada entidade tem seu próprio bloco de campos livres. A lista vem do mesmo
   lugar nos dois lados: o servidor manda `editaveis`, e aqui só se escolhe o
   rótulo e de onde tirar o valor atual. */
function camposLivres(cat, reg) {
  const extra = reg.extra || {};
  const puxar = (campo) => (extra[campo] !== undefined ? extra[campo] : reg[campo]);
  switch (cat) {
    case 'empresa': case 'usuario':
      return [
        ['nome_alterado', 'Nome corrigido', puxar('nome_alterado'),
         'não altera o nome da ANAC: registra a correção'],
        ['uf_alterada', 'UF corrigida', puxar('uf_alterada')],
        ['justificativa', 'Justificativa da correção', puxar('justificativa')],
        ['telefone', 'Telefone', puxar('telefone')],
        ['email', 'E-mail', puxar('email')],
        ['logo_url', 'Logo (URL)', puxar('logo_url')],
        ['end_numero', 'Número', puxar('end_numero')],
        ['end_bairro', 'Bairro', puxar('end_bairro')],
      ];
    case 'aeronave':
      return [
        ['matricula', 'Matrícula', reg.matricula_ed],
        ['modelo', 'Modelo', reg.modelo_ed],
        ['fabricante', 'Fabricante', reg.fabricante_ed],
        ['numero_serie', 'Número de série', reg.numero_serie_ed],
        ['ano_fabricacao', 'Ano de fabricação', reg.ano_fabricacao_ed],
        ['classe', 'Classe', reg.classe_ed],
        ['tipo_icao', 'Tipo de operação ICAO', reg.tipo_icao_ed],
        ['motivo_cancelamento', 'Motivo do cancelamento', reg.motivo_cancelamento_ed],
      ];
    case 'aerodromo':
      return [
        ['operador', 'Operador', reg.operador_ed, 'não existe em `aerodromo`'],
        ['observacao', 'Observação', reg.observacao_ed, 'não existe em `aerodromo`'],
        ['nome', 'Nome', reg.nome_ed], ['municipio', 'Município', reg.municipio_ed],
        ['uf', 'UF', reg.uf_ed], ['tipo', 'Tipo', reg.tipo_ed],
        ['situacao', 'Situação', reg.situacao_ed],
      ];
    default: return [];
  }
}

/* Tabelas dentro da página da aeronave. Mesma ordem da lista: matrícula
   primeiro, número de série em coluna própria. */
const COLS_AERONAVE = [['matricula', 'Matrícula / Marca (prefixo)'],
  ['pessoa_nome', 'Pessoa'], ['papel', 'Papel'], ['percentual', '%', true],
  ['snapshot_mes', 'Mês'], ['operacao_121', '121'], ['operacao_135', '135'],
  ['transp_reg_135', '135 reg.']];
const COLS_VINCULO = [['matricula', 'Matrícula'], ['numero_serie', 'Nº de série'],
  ['pessoa_nome', 'Pessoa'], ['papel', 'Papel'], ['percentual', '%', true],
  ['snapshot_mes', 'Mês']];

function blocoHistorico(aud) {
  if (!aud || !aud.length) return '';
  return `<div class="caixa"><h3>Histórico de alterações</h3>`
    + aud.map(a => `<div style="font-size:12.3px;margin-bottom:6px">
        <small>${esc(a.em)} · ${esc(a.por || '')}</small><br>
        <b>${esc(a.campo)}</b>: ${esc(a.antes == null ? 'vazio' : a.antes)}
        → ${esc(a.depois == null ? 'vazio' : a.depois)}</div>`).join('')
    + `</div>`;
}

function botaoEditar() {
  return `<div class="barra" style="margin-top:4px">
    <button class="azul" id="editar">Editar campos livres</button>
    <small class="nota" style="margin:0">o dado oficial da ANAC não é editável</small>
  </div>`;
}

function blocoPessoa(d) {
  const t = d.totais || {};
  let h = `<div class="caixa"><h3>Identificação</h3><dl class="kv">
    ${par('Identificador', d.id)}
    ${par('Tipo', d.natureza === 'FISICA' ? 'pessoa física' : 'empresa')}
    ${par('Nome na fonte da ANAC', d.nome_fonte || d.nome, seloAnac)}
    ${par('Nome exibido', d.nome, d.nome !== d.nome_fonte ? seloEditado : null)}
    ${par('Chave de identidade', d.chave)}
    ${par('Documento', d.documento_bruto || d.documento,
          d.documento_mascarado ? '<span class="selo s-erro">mascarado na fonte</span>'
          : (d.documento ? seloAnac : null))}
    ${par('UF', d.uf, d.uf !== d.uf_fonte ? seloEditado : (d.uf ? seloAnac : null))}
    ${d.fabricante ? par('Fabricante cadastrado', d.fabricante.org_nabrev
        || d.fabricante.org_codigo) : ''}
    ${d.org_producao ? par('Organização de produção', d.org_producao.razao_social,
        seloAnac) : ''}
    ${par('Vínculos', nf(t.vinculos), null)}
    ${par('Como proprietário', nf(t.propriedades))}
    ${par('Como operador', nf(t.operacoes))}
    ${par('Drones (SISANT)', nf(t.drones))}
    ${d.socios && d.socios.length ? par('Sócios', nf(d.socios.length)) : ''}
    ${par('Editado em', d.editado_em, d.editado_em ? seloEditado : null)}
  </dl></div>`;

  if (d.aviso_cpf) {
    h += `<div class="aviso"><b>Atenção:</b> ${esc(d.aviso_cpf)}</div>`;
  }
  const doc = d.extra || {};
  const socio = [['razao_social', 'Razão social'], ['nome_fantasia', 'Nome fantasia'],
                 ['telefone', 'Telefone'], ['email', 'E-mail'],
                 ['site', 'Site'], ['logo_url', 'Logo (URL)'],
                 ['end_logradouro', 'Logradouro'], ['end_complemento', 'Complemento'],
                 ['end_cep', 'CEP'], ['end_municipio', 'Município'],
                 ['end_uf', 'UF'], ['end_pais', 'País']];
  const temDado = socio.some(([c]) => doc[c]);
  if (temDado) {
    h += `<div class="caixa"><h3>Societário e contato</h3><dl class="kv">`
      + socio.map(([c, r]) => par(r, doc[c],
          doc[c] ? (doc[c + '_origem'] === 'ANAC' ? seloAnac : seloEditado) : null
        )).join('') + `</dl></div>`;
  }
  if (d.socios) {
    h += `<div class="caixa"><h3>Sócios</h3>
      <p class="nota">Não existe QSA nos dados abertos da ANAC e o CPF vem sempre
      mascarado. Estes registros são sempre <em>manuais</em>, preenchidos a partir
      de ficha societária.</p>`
      + (d.socios.length ? tabela(
          [['socio_nome', 'Nome'], ['socio_cpf', 'CPF'], ['qual_cargo', 'Cargo'],
           ['participacao_pct', '%', true], ['origem', 'Origem']], d.socios)
        : '<p class="vazio">Nenhum sócio cadastrado.</p>')
      + `</div>`;
  }
  if (d.vincculos && d.vincculos.length) {
    h += `<div class="caixa"><h3>Aeronaves deste cadastro</h3>
      <p class="nota">${nf(t.propriedades)} como proprietário e
      ${nf(t.operacoes)} como operador, no histórico inteiro do RAB.
      Clique numa linha para abrir a aeronave.</p>`
      + tabela(COLS_VINCULO, d.vincculos, {cat: 'aeronave'}) + `</div>`;
  }
  if (d.resumo_vinculos && d.resumo_vinculos.length) {
    h += `<div class="caixa"><h3>Vínculos por mês e papel</h3>`
      + tabela([['snapshot_mes', 'Mês'], ['papel', 'Papel'], ['n', 'Vínculos', true]],
               d.resumo_vinculos) + `</div>`;
  }
  if (d.drones && d.drones.length) {
    h += `<div class="caixa"><h3>Drones registrados (SISANT)</h3>`
      + tabela([['codigo_aeronave', 'Código'], ['modelo_nome', 'Modelo'],
                ['tipo_uso', 'Uso'], ['data_validade', 'Validade']], d.drones,
               {cat: 'drone'}) + `</div>`;
  }
  return h;
}

function blocoAeronave(d) {
  const t = d.totais || {};
  let h = `<div class="caixa"><h3>Identificação</h3><dl class="kv">
    ${par('Matrícula / Marca (prefixo)', d.matricula_exibida, seloAnac)}
    ${d.matricula_oficial_vazia ? `<p class="nota">A fonte da ANAC não traz o
       certificado de matrícula desta aeronave. A identificação acima foi montada
       com prefixo e número de série, que também são dados da fonte.</p>` : ''}
    ${par('Modelo na fonte', d.ds_modelo, seloAnac)}
    ${par('Fabricante na fonte', d.nm_fabricante, seloAnac)}
    ${d.fabricante ? par('Fabricante cadastrado',
        d.fabricante.razao_social || d.fabricante.nome, seloAnac) : ''}
    ${d.modelo ? par('Modelo no catálogo',
        `${d.modelo.ds_modelo || ''} · ${d.modelo.marca || ''}`, seloAnac) : ''}
    ${par('Número de série', d.nr_serie, seloAnac)}
    ${par('Ano de fabricação', d.nr_ano_fabricacao, seloAnac)}
    ${par('Classe (CD_CLS)', d.cd_classe, seloAnac)}
    ${par('Tipo de operação ICAO', d.cd_tipo_icao, seloAnac)}
    ${par('Tipo de operação', d.tp_operacao, seloAnac)}
    ${par('Passageiros (máx.)', d.nr_passageiros_max, seloAnac)}
    ${par('Tripulação mínima', d.nr_tripulacao_min, seloAnac)}
    ${par('Validade do CVA', d.dt_validade_cva, seloAnac)}
    ${par('Validade do CA', d.dt_validade_ca, seloAnac)}
    ${par('Cancelamento', d.dt_cancelamento, seloAnac)}
    ${par('Motivo do cancelamento', d.ds_motivo_cancelamento, seloAnac)}
    ${par('Gravame', d.ds_gravame, seloAnac)}
    ${par('Snapshot', d.snapshot_mes, seloAnac)}
    ${par('Vínculos', nf(t.vinculos))}
    ${par('Proprietários', nf(t.proprietarios))}
    ${par('Operadores', nf(t.operadores))}
    ${par('Meses na série', nf(t.meses))}
  </dl></div>`;
  if (d.vincculos && d.vincculos.length) {
    h += `<div class="caixa"><h3>Proprietários e operadores</h3>
      <p class="nota">Base normativa do bloco 121/135:
      <span class="cite">RBAC 45.12-I(a)</span> — uma pessoa só pode operar uma
      aeronave sob o RBAC 135 se na aeronave estiver inscrita TRANSPORTE PÚBLICO.
      Uma linha da pessoa abre o cadastro dela.</p>`
      + tabela(COLS_VINCULO, d.vincculos, {cat: 'pessoa'}) + `</div>`;
  }
  h += blocoFonte(d, ['modelo', 'fabricante', 'vincculos', 'totais']);
  return h;
}

function blocoAerodromo(d) {
  const rs = d.redemet_status;
  let h = `<div class="caixa"><h3>Identificação</h3><dl class="kv">
    ${par('OACI', d.icao, seloAnac)}
    ${par('CIAD', d.ciad, seloAnac)}
    ${par('Nome', d.nome_ed, seloAnac)}
    ${par('Município', d.municipio_ed, seloAnac)}
    ${par('UF', d.uf_ed, seloAnac)}
    ${par('Tipo', d.tipo_ed, seloAnac)}
    ${par('Situação', d.situacao_ed, seloAnac)}
    ${par('Operação diurna', d.operacao_diurna, seloAnac)}
    ${par('Operação noturna', d.operacao_noturna, seloAnac)}
    ${par('Município servido', d.municipio_servido, seloAnac)}
    ${par('UF servida', d.uf_servido, seloAnac)}
    ${par('Altitude', d.altitude, seloAnac)}
    ${par('Latitude', d.latitude, seloAnac)}
    ${par('Longitude', d.longitude, seloAnac)}
    ${par('Validade do registro', d.validade_registro, seloAnac)}
    ${par('Operador (não existe na fonte)', d.operador_ed, d.operador_ed ? seloEditado : null)}
    ${par('Observação (não existe na fonte)', d.observacao_ed, d.observacao_ed ? seloEditado : null)}
  </dl></div>`;

  if (rs) {
    h += `<div class="caixa"><h3>REDEMET — situação meteorológica</h3><dl class="kv">
      ${par('Cor do serviço', rs.cor)}
      ${par('Nome na REDEMET', rs.nome)}
      ${par('Latitude da REDEMET', rs.lat)}
      ${par('Longitude da REDEMET', rs.lon)}
      ${par('Colhido em', rs.colhido_em)}
    </dl>`;
    if (rs.mensagem) {
      h += `<h3>Mensagem do serviço</h3><pre class="doc">${esc(rs.mensagem)}</pre>`;
    }
    h += `</div>`;
  }
  if (d.redemet_mensagens && d.redemet_mensagens.length) {
    h += `<div class="caixa"><h3>Mensagens METAR e TAF</h3>`
      + tabela([['tipo', 'Tipo'], ['validade_inicial', 'Validade inicial'],
                ['validade_final', 'Validade final']], d.redemet_mensagens)
      + d.redemet_mensagens.map(m => `<h3>${esc(m.tipo)} · ${esc(m.validade_inicial || '')}</h3>
          <pre class="doc">${esc(m.mensagem || '')}</pre>`).join('')
      + `</div>`;
  }
  h += blocoHidratar(d);
  h += blocoFonte(d, ['redemet_status', 'redemet_mensagens', 'mensagens_por_tipo']);
  return h;
}

/* ---------------------------------------------------------- hidratação */
function blocoHidratar(d) {
  const fontes = d.hidratacao || [];
  return `<div class="caixa">
    <h3>Hidratação — REDEMET e AISWEB</h3>
    <p class="nota">Consulta, para este aeródromo, tudo o que as duas APIs
    permitem. Cada resposta só é gravada quando o conteúdo **realmente mudou**
    (comparação por sha256): reidratar o mesmo aeródromo custa as consultas e
    zero gravações. Nada disso altera a tabela <code>aerodromo</code> — a fonte
    oficial da ANAC fica intacta e a divergência aparece lado a lado.</p>
    <div class="barra">
      <button class="primario" id="hidratar">Hidratar este aeródromo</button>
      <button id="ver-hidratacao">Recarregar o que já existe</button>
      <small id="msg-hidratacao" class="msg"></small>
    </div>
    <div id="tabela-hidratacao">${
      fontes.length ? tabelaHidratacao(fontes)
        : '<p class="vazio">Ainda não hidratado.</p>'}</div>
  </div>`;
}

function tabelaHidratacao(fontes) {
  return `<table><thead><tr><th>Fonte</th><th>Serviço</th><th>Estado</th>
    <th class="num">Bytes</th><th>Colhido em</th></tr></thead><tbody>`
    + fontes.map(f => `<tr><td><b>${esc(f.fonte)}</b></td>
        <td>${esc(f.endpoint)}</td>
        <td>${f.status === 'ok' ? '<span class="selo s-ok">ok</span>'
             : f.status === 'inalterado' ? '<span class="selo s-na">sem mudança</span>'
             : f.status === 'sem_chave'
               ? '<span class="selo s-lock">falta credencial</span>'
               : `<span class="selo s-erro">${esc(f.status)}</span>`}</td>
        <td class="num">${nf(f.bytes)}</td><td><small>${esc(f.coletado_em || '')}</small></td>
      </tr>
      <tr><td colspan="5"><small class="nota" style="margin:0">${
        esc(resumoTexto(f.resumo))}</small>${
        f.resumo && (f.resumo.divergencia_lat || f.resumo.divergencia_lon)
          ? `<br><small style="color:var(--warn)">divergência de coordenada: ${
              esc(f.resumo.divergencia_lat || f.resumo.divergencia_lon)}</small>` : ''}</td></tr>`).join('')
    + `</tbody></table>`;
}

function resumoTexto(resumo) {
  if (!resumo) return '';
  const partes = [];
  if (resumo.rotulo) partes.push(resumo.rotulo);
  if (resumo.itens != null) partes.push(`${resumo.itens} registro(s)`);
  if (resumo.mensagens != null) partes.push(`${resumo.mensagens} mensagem(ns)`);
  if (resumo.cor) partes.push(`cor=${resumo.cor}`);
  if (resumo.erro) partes.push(`erro: ${resumo.erro}`);
  return partes.join(' · ');
}

function blocoSimples(d, titulo, campos) {
  const t = d.totais || {};
  let h = `<div class="caixa"><h3>${esc(titulo)}</h3><dl class="kv">`;
  for (const [rot, chave, fmt] of campos) {
    const v = d[chave];
    h += par(rot, fmt ? fmt(v) : v, v == null || v === '' ? null : seloAnac);
  }
  if (t.aeronaves != null) h += par('Aeronaves', nf(t.aeronaves));
  if (t.modelos != null) h += par('Modelos', nf(t.modelos));
  h += `</dl></div>`;
  return h;
}

function blocoFabricante(d) {
  let h = blocoPessoa(d);
  h += blocoSimples(d, 'Fabricante', [
    ['Código da organização', 'org_codigo'], ['Abreviatura', 'org_nabrev'],
    ['Chave da pessoa', 'chave']]);
  if (d.certificada) {
    h += `<div class="caixa"><h3>Empresa certificada</h3><dl class="kv">
      ${par('Razão social', d.certificada.razao_social, seloAnac)}
      ${par('Nome fantasia', d.certificada.nome_fantasia, seloAnac)}
      ${par('Certificado', d.certificada.certificado, seloAnac)}
      ${par('Tipo', d.certificada.tipo, seloAnac)}
    </dl></div>`;
  }
  if (d.modelos && d.modelos.length) {
    h += `<div class="caixa"><h3>Modelos</h3>`
      + tabela([['ds_modelo', 'Modelo'], ['marca', 'Marca'],
                ['n_aeronaves', 'Aeronaves', true]], d.modelos, {cat: 'modelo'})
      + `</div>`;
  }
  if (d.marcas && d.marcas.length) {
    h += `<div class="caixa"><h3>Marcas</h3>`
      + tabela([['nome', 'Marca'], ['n_modelos', 'Modelos', true]], d.marcas,
               {cat: 'marca'}) + `</div>`;
  }
  return h;
}

function blocoModelo(d) {
  let h = blocoSimples(d, 'Modelo', [
    ['Designação', 'ds_modelo'], ['Código de tipo', 'cd_tipo'],
    ['Marca', 'marca'], ['Fabricante', 'fabricante_razao' || 'fabricante_nome'],
    ['Código do fabricante', 'org_codigo'], ['Abreviatura', 'org_nabrev']]);
  if (d.aeronaves && d.aeronaves.length) {
    h += `<div class="caixa"><h3>Aeronaves deste modelo</h3>`
      + tabela([['matricula', 'Matrícula'], ['nr_serie', 'Série'],
                ['ano_fabricacao', 'Ano'], ['tp_operacao', 'Operação'],
                ['snapshot_mes', 'Mês']].map(c => c[1] === 'Ano'
                  ? ['nr_ano_fabricacao', 'Ano'] : c), d.aeronaves,
               {cat: 'aeronave'}) + `</div>`;
  }
  if (d.classes && d.classes.length) {
    h += `<div class="caixa"><h3>Frota por classe</h3>`
      + tabela([['classe', 'Classe'], ['n', 'Aeronaves', true]], d.classes) + `</div>`;
  }
  return h;
}

function blocoMarca(d) {
  let h = blocoSimples(d, 'Marca', [['Nome da marca', 'nome']]);
  if (d.modelos && d.modelos.length) {
    h += `<div class="caixa"><h3>Modelos</h3>`
      + tabela([['ds_modelo', 'Modelo'], ['fabricante', 'Fabricante'],
                ['n_aeronaves', 'Aeronaves', true]], d.modelos, {cat: 'modelo'})
      + `</div>`;
  }
  if (d.aeronaves_por_mes && d.aeronaves_por_mes.length) {
    h += `<div class="caixa"><h3>Frota por mês</h3>`
      + tabela([['snapshot_mes', 'Mês'], ['n', 'Aeronaves', true]],
               d.aeronaves_por_mes) + `</div>`;
  }
  return h;
}

function blocoDrone(d) {
  let h = `<div class="caixa"><h3>Registro SISANT</h3><dl class="kv">
    ${par('Código da aeronave', d.codigo_aeronave, seloAnac)}
    ${par('Validade', d.data_validade, seloAnac)}
    ${par('Tipo de uso', d.tipo_uso, seloAnac)}
    ${par('Fabricante', d.fabricante_nome, seloAnac)}
    ${par('Modelo', d.modelo_nome, seloAnac)}
    ${par('Número de série', d.num_serie, seloAnac)}
    ${par('Peso máximo (kg)', d.peso_max_kg, seloAnac)}
    ${par('Ramo de atividade', d.ramo_atividade, seloAnac)}
  </dl></div>`;
  h += `<div class="caixa"><h3>Responsável</h3><dl class="kv">
    ${par('Nome', d.responsavel, seloAnac)}
    ${par('CPF/CNPJ', d.responsavel_documento, d.responsavel_documento ? seloAnac : null)}
    ${par('Natureza', d.responsavel_natureza, seloAnac)}
    ${par('UF', d.resp_uf, seloAnac)}
    ${d.pessoa_id ? `<dt>Cadastro</dt><dd><a href="#/${
      d.responsavel_natureza === 'FISICA' ? 'usuario' : 'empresa'}/${d.pessoa_id}">abrir pessoa</a></dd>` : ''}
  </dl></div>`;
  if (d.mesmo_modelo) {
    h += `<div class="caixa"><h3>Mesmo modelo na base</h3><dl class="kv">
      ${par('Drone(s) do mesmo modelo', nf(d.mesmo_modelo.n))}
      ${par('Peso médio (kg)', dec(d.mesmo_modelo.peso_medio, 2))}
      ${par('Drone(s) do mesmo fabricante', nf(d.mesmo_fabricante && d.mesmo_fabricante.n))}
    </dl></div>`;
  }
  return h;
}

function blocoVinculo(d) {
  let h = `<div class="caixa"><h3>Vínculo</h3><dl class="kv">
    ${par('Identificador', d.id)}
    ${par('Papel', d.papel, seloAnac)}
    ${par('Percentual', d.percentual, seloAnac)}
    ${par('UF', d.uf, seloAnac)}
    ${par('Snapshot', d.snapshot_mes, seloAnac)}
    ${par('Operação RBAC 121', booleano(d.operacao_121), seloAnac)}
    ${par('Operação RBAC 135', booleano(d.operacao_135), seloAnac)}
    ${par('Transporte público 121', booleano(d.transp_reg_121), seloAnac)}
    ${par('Transporte público 135', booleano(d.transp_reg_135), seloAnac)}
    ${par('SAE', booleano(d.sae), seloAnac)}
    ${par('Autstrut', booleano(d.authistrut), seloAnac)}
  </dl>`;
  if (d.regra) {
    h += `<p class="nota" style="margin-top:11px">${esc(d.regra)}</p></div>`;
  } else {
    h += `</div>`;
  }
  h += `<div class="caixa"><h3>Aeronave</h3><dl class="kv">
    ${par('Matrícula', d.matricula, seloAnac)}
    ${par('Modelo', d.modelo, seloAnac)}
    ${par('Fabricante', d.nm_fabricante, seloAnac)}
    ${par('Tipo de operação', d.tp_operacao, seloAnac)}
    <dt>Cadastro</dt><dd><a href="#/aeronave/${d.aeronave_id}">abrir aeronave</a></dd>
  </dl></div>`;
  const catPessoa = d.natureza === 'FISICA' ? 'usuario' : 'empresa';
  h += `<div class="caixa"><h3>Pessoa</h3><dl class="kv">
    ${par('Nome', d.pessoa_nome, seloAnac)}
    ${par('Razão social', d.razao_social, seloAnac)}
    ${par('Natureza', d.natureza, seloAnac)}
    ${par('Documento', d.documento, seloAnac)}
    <dt>Cadastro</dt><dd><a href="#/${catPessoa}/${d.pessoa_id}">abrir pessoa</a></dd>
  </dl></div>`;
  if (d.outros_papeis && d.outros_papeis.length) {
    h += `<div class="caixa"><h3>Outros papéis na mesma aeronave</h3>`
      + tabela([['pessoa_nome', 'Pessoa'], ['papel', 'Papel'],
                ['percentual', '%', true], ['snapshot_mes', 'Mês']],
               d.outros_papeis, {cat: 'pessoa'}) + `</div>`;
  }
  return h;
}

/* ------------------------------------------------------------- montagem */
function montarDetalhe(d) {
  let h = '';
  switch (ROTA.cat) {
    case 'empresa': h = blocoPessoa(d); break;
    case 'usuario': h = blocoPessoa(d); break;
    case 'fabricante': h = blocoFabricante(d); break;
    case 'aeronave': h = blocoAeronave(d); break;
    case 'aerodromo': h = blocoAerodromo(d); break;
    case 'modelo': h = blocoModelo(d); break;
    case 'marca': h = blocoMarca(d); break;
    case 'drone': h = blocoDrone(d); break;
    case 'vinculo': h = blocoVinculo(d); break;
    default: h = blocoFonte(d, []);
  }
  const livres = camposLivres(ROTA.cat, d);
  if (livres.length) {
    const quantos = livres.filter(([n]) => !(d.bloqueados || []).includes(n)).length;
    h += botaoEditar();
    if (quantos === 0) {
      h += `<div class="aviso" style="margin-top:11px">Nenhum campo deste registro
        está livre: todos os campos que a interface conhece já têm valor nos
        arquivos da ANAC. A correção de identidade (quando aplicável) continua
        disponível — ela registra a divergência sem alterar a fonte.</div>`;
    }
    h += blocoEdicao(d, livres);
  }
  h += blocoHistorico(d.auditoria);
  return h;
}

/* ------------------------------------------------------------- ligação */
function ligarDetalhe(d) {
  ligarPuxadas('#corpo');
  const campos = camposLivres(ROTA.cat, d);
  const editar = $('#editar');
  if (editar && campos.length) {
    editar.addEventListener('click', () => {
      const caixa = $('#caixa-edicao');
      caixa.hidden = false;
      editar.style.display = 'none';
      caixa.scrollIntoView({behavior: 'smooth', block: 'nearest'});
    });
  }
  const salvar = $('#salvar');
  if (salvar) {
    salvar.addEventListener('click', async () => {
      const corpo = {};
      $$('#caixa-edicao input[name]').forEach(i => { corpo[i.name] = i.value; });
      const rotaEdicao = {empresa: 'pessoa', usuario: 'pessoa',
        aeronave: 'aeronave', aerodromo: 'aerodromo'}[ROTA.cat];
      const msg = $('#msg');
      salvar.disabled = true;
      try {
        const r = await put(`/api/catalogo/${rotaEdicao}/${encodeURIComponent(d.key ?? d.id ?? d.icao)}`, corpo);
        msg.className = 'msg ok';
        msg.textContent = r.alterado
          ? `Salvo: ${r.campos.join(', ')}.`
          : 'Nada mudou — nenhum campo difere do que já estava gravado.';
        if (r.alterado) await telaDetalhe();
      } catch (e) {
        msg.className = 'msg ruim';
        msg.textContent = e.message;
        salvar.disabled = false;
      }
    });
  }
  const cancelar = $('#cancelar');
  if (cancelar) {
    cancelar.addEventListener('click', async () => {
      const editar = $('#editar');
      $('#caixa-edicao').hidden = true;
      if (editar) editar.style.display = '';
    });
  }
  if (ROTA.cat === 'aerodromo') ligarHidratacao(d);
}

function ligarHidratacao(d) {
  const icao = d.icao;
  const alvo = $('#tabela-hidratacao');
  const msg = $('#msg-hidratacao');
  const b = $('#hidratar');
  b.addEventListener('click', async () => {
    b.disabled = true;
    msg.className = 'msg';
    msg.textContent = 'consultando REDEMET e AISWEB…';
    try {
      const r = await api(`/api/hidratacao/${encodeURIComponent(icao)}`, {method: 'POST'});
      const linhas = (r.relatorio || [])
        .map(x => `<li>${esc(x.fonte)} · ${esc(x.endpoint)} — ${esc(x.status)}: ${esc(x.detalhe || '')}</li>`)
        .join('');
      msg.className = 'msg ok';
      msg.textContent = `${r.novos} registro(s) novos, ${r.iguais} idêntico(s).`;
      alvo.innerHTML = tabelaHidratacao(r.fontes || [])
        + `<details><summary>relatório da ida</summary><ul style="font-size:12.3px">${linhas}</ul></details>`;
      pintarMenu();
      ESTADO.atualizacao = await api('/api/atualizacao/estado');
      pintarMenu();
    } catch (e) {
      msg.className = 'msg ruim';
      msg.textContent = e.message;
    }
    b.disabled = false;
  });
  $('#ver-hidratacao').addEventListener('click', async () => {
    try {
      alvo.innerHTML = tabelaHidratacao(await api(`/api/hidratacao/${encodeURIComponent(icao)}`));
    } catch (e) { msg.className = 'msg ruim'; msg.textContent = e.message; }
  });
}

/* ------------------------------------------------- configurações */
const NOTAS = {
  empresa: 'Busque por nome, razão social, nome fantasia, CNPJ, site ou e-mail. Clique numa linha para abrir o cadastro completo, com as aeronaves, o contato e os campos que a ANAC não traz.',
  usuario: 'Pessoas físicas da base. O CPF vem mascarado da ANAC e não há QSA nos dados abertos: a identidade aqui é nome + UF. Clique numa linha para o cadastro e as aeronaves.',
  aeronave: 'Clique numa aeronave para ver a identificação completa da fonte, os proprietários e operadores, e corrigir o que a fonte não traz.',
  fabricante: 'Fabricantes modelados como empresa. Clique para ver os modelos, as marcas e a empresa certificada.',
  modelo: 'Modelos do catálogo, com a marca e o fabricante. Clique para ver asubonaves daquele modelo.',
  marca: 'Marcas do catálogo. A marca aqui é o prefixo de matrícula do RAB, não o nome comercial.',
  aerodromo: 'Clique num aeródromo para o cadastro completo, a situação REDEMET e a hidratação com AISWEB.',
  drone: 'Drones registrados no SISANT. Clique para o registro completo e o responsável.',
  vinculo: 'Cada linha é a relação pessoa–aeronave com participação fracionária e os flags 121/135. Clique para ver a aeronave e a pessoa.',
};
const DICA = {
  empresa: 'nome, razão social, CNPJ, site, e-mail', usuario: 'nome ou CPF',
  aeronave: 'matrícula, modelo, fabricante, série', fabricante: 'nome, CNPJ, abreviatura',
  modelo: 'designação, marca, fabricante', marca: 'prefixo da marca',
  aerodromo: 'OACI, nome, município', drone: 'código, modelo, fabricante, responsável',
  vinculo: 'matrícula, pessoa, documento, papel',
};

async function telaAtualizacoes() {
  const s = await api('/api/atualizacao/estado');
  ESTADO.atualizacao = s;
  pintarMenu();
  const pastas = s.pastas || {};
  $('#tela').innerHTML = `
    <h1>Configurações · Atualizações</h1>
    <p class="nota">A verificação lê a pasta <code>update/&lt;yyyymmdd&gt;/</code> mais
    recente, compara cada arquivo com os cadastros e grava <b>somente o que
    difere</b>. O intervalo é medido em minutos decorridos desde a última
    verificação gravada no banco — por isso a contagem continua correta depois de
    dias com o servidor desligado.</p>
    <div class="sema">
      <span class="luz ${s.cor}"></span>
      <span class="txt"><b>${esc(s.rotulo)}</b>
        <small>${esc(s.etapa ? s.etapa + ' — ' : '')}${esc(s.mensagem || '')}</small></span>
      <span id="conta">${contagem(s)}</span>
    </div>
    <div class="caixa">
      <h3>Automático</h3>
      <div class="barra">
        <label class="nota" style="margin:0">a cada
          <input type="number" id="intervalo" min="5" max="525600" style="width:110px"
                 value="${s.intervalo_minutos}"> minutos</label>
        <button class="primario" id="salvar-cfg">Salvar intervalo</button>
        <label class="nota" style="margin:0">
          <input type="checkbox" id="auto" ${s.auto_ligado ? 'checked' : ''}>
          verificação automática ligada</label>
        <button id="executar">Verificar agora</button>
      </div>
      <dl class="kv">
        ${par('Última verificação', s.ultima_verificacao_em)}
        ${par('Pasta já conferida', s.pasta_aplicada)}
        ${par('Pasta mais recente', pastas.pasta || 'nenhuma com data')}
        ${par('Pastas disponíveis', (pastas.pastas || []).join(', ') || '—')}
        ${par('Pastas ignoradas (fora do formato)', (pastas.ignoradas || []).join(', ') || '—')}
        ${par('Registros conferidos', nf(s.registros))}
        ${par('Campos alterados', nf(s.campos))}
        ${par('Registros novos', nf(s.novos))}
      </dl>
      <p class="msg" id="msg-cfg"></p>
    </div>
    <div class="caixa">
      <h3>Histórico das verificações</h3>
      <div id="historico"><p class="vazio">carregando…</p></div>
    </div>
    <div class="caixa">
      <h3>Links de arquivos e metadados</h3>
      <p class="nota">Aceita <code>json</code>, <code>csv</code>, <code>doc</code>,
      <code>docx</code>, <code>xls</code>, <code>xlsx</code>, <code>pdf</code>,
      <code>txt</code> e <code>md</code>. <code>csv</code>, <code>txt</code>,
      <code>docx</code> e <code>xlsx</code> são convertidos para Markdown na
      própria tela (ZIP + XML, sem instalar nada). <code>doc</code>,
      <code>xls</code> binário e <code>pdf</code> são preservados e catalogados,
      com o motivo escrito — não são convertidos.</p>
      <div class="barra">
        <input type="text" id="f-url" placeholder="https://.../arquivo.csv" style="flex:1;min-width:250px">
        <input type="text" id="f-titulo" placeholder="título (opcional)" style="width:200px">
        <select id="f-formato"></select>
        <button class="primario" id="f-add">Adicionar</button>
      </div>
      <p class="msg" id="msg-fonte"></p>
      <div id="lista-fontes"><p class="vazio">carregando…</p></div>
    </div>`;

  $('#f-formato').innerHTML = '<option value="">formato pela extensão</option>'
    + ['json','csv','doc','docx','xls','xlsx','pdf','txt','md']
      .map(f => `<option value="${f}">.${f}</option>`).join('');
  $('#salvar-cfg').addEventListener('click', async () => {
    await gravarCfg({intervalo_minutos: $('#intervalo').value});
  });
  $('#auto').addEventListener('change', async () => {
    await gravarCfg({auto_ligado: $('#auto').checked});
  });
  $('#executar').addEventListener('click', executarAgora);
  $('#f-add').addEventListener('click', adicionarFonte);
  await Promise.all([carregarHistorico(), carregarFontes()]);
  iniciarContagem();
}

/* --------------------------------------------------------------- acesso */
/* A tela de login não consulta nada além de `POST /api/sessao`. O formulário de
   recuperação é o mesmo componente com outro botão, porque as duas coisas são
   "e-mail + mensagem"; separá-los duplicaria o tratamento de erro. */
function telaLogin() {
  document.body.classList.add('sem-sessao');
  $('#app').style.display = 'none';
  const alvo = $('#acesso');
  alvo.style.display = 'block';
  alvo.innerHTML = `
    <div class="acesso">
      <div class="caixa">
        <h1>Registro ANAC</h1>
        <p class="nota">Entre com seu e-mail e senha. A sessão vale por 12 horas.</p>
        <p class="msg" id="msg-acesso"></p>
        <label class="campo">E-mail
          <input type="email" id="email" autocomplete="username" autofocus></label>
        <label class="campo">Senha
          <input type="password" id="senha" autocomplete="current-password"></label>
        <div class="barra" style="margin:14px 0 0">
          <button class="primario" id="entrar">Entrar</button>
          <button id="recuperar">Recuperar senha</button>
          <button id="sair-form" style="display:none">Voltar ao login</button>
        </div>
        <p class="nota" id="dica-acesso" style="margin:16px 0 0"></p>
      </div>
    </div>`;

  const msg = $('#msg-acesso');
  const mostrar = (texto, ruim) => {
    msg.className = 'msg' + (ruim ? ' ruim' : ' ok');
    msg.textContent = texto;
  };
  const email = $('#email'), senha = $('#senha');

  $('#entrar').addEventListener('click', async () => {
    mostrar('entrando…', false);
    try {
      const r = await api('/api/sessao', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: email.value.trim(), senha: senha.value})});
      ESTADO.usuario = r.usuario;
      ESTADO.sessaoResolvida = true;
      if (r.usuario.troca_senha_obrigatoria) {
        location.hash = '#/configuracoes/senha';
        mostrar('primeiro acesso: defina uma nova senha', false);
        await arrancar();
        return;
      }
      await arrancar();
    } catch (e) {
      mostrar(e.message, true);
      senha.value = '';
      senha.focus();
    }
  });

  $('#recuperar').addEventListener('click', async () => {
    if (!$('#email').value.trim()) return mostrar('digite o e-mail da conta', true);
    mostrar('gerando o link…', false);
    try {
      const r = await api('/api/sessao/recuperar', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({email: email.value.trim()})});
      mostrar(r.mensagem, false);
      // Sem SMTP configurado o link é devolvido aqui e gravado em
      // `build/recuperacao-pendente.log`. Mostrar é melhor que fingir envio.
      $('#dica-acesso').innerHTML = r.link_local
        ? `<div class="caixa" style="margin:10px 0 0">
             <b>Envio por e-mail indisponível</b><br>${esc(r.motivo || '')}<br>
             Use este endereço para definir a senha agora:
             <div style="word-break:break-all;margin:6px 0">
               <input type="text" readonly value="${esc(r.link_local)}"
                      style="width:100%" onclick="this.select()"></div>
           </div>`
        : '';
    } catch (e) { mostrar(e.message, true); }
  });

  $('#sair-form').addEventListener('click', () => telaRecuperar());
  senha.addEventListener('keydown', e => { if (e.key === 'Enter') $('#entrar').click(); });
  email.addEventListener('keydown', e => { if (e.key === 'Enter') senha.focus(); });
}

/* Formulário de nova senha. Aparece por link (`#/redefinir?token=…`) e por
   configuração (`#/configuracoes/senha`), com uma diferença: no link não há
   sessão, então o token é o que autoriza. */
function telaRedefinir() {
  const token = new URLSearchParams(
    (location.hash.split('?')[1] || '')).get('token') || '';
  if (!token) { location.hash = '#/configuracoes/senha'; return; }
  document.body.classList.add('sem-sessao');
  $('#app').style.display = 'none';
  const alvo = $('#acesso');
  alvo.style.display = 'block';
  alvo.innerHTML = `
    <div class="acesso">
      <div class="caixa">
        <h1>Nova senha</h1>
        <p class="nota">O link vale uma vez só. A nova senha precisa de pelo menos
        8 caracteres, uma letra e um número.</p>
        <p class="msg" id="msg-acesso"></p>
        <label class="campo">Nova senha
          <input type="password" id="nova" autocomplete="new-password" autofocus></label>
        <label class="campo">Repetir a senha
          <input type="password" id="nova2" autocomplete="new-password"></label>
        <div class="barra" style="margin:14px 0 0">
          <button class="primario" id="gravar">Gravar nova senha</button>
        </div>
      </div>
    </div>`;
  $('#gravar').addEventListener('click', async () => {
    const msg = $('#msg-acesso');
    if ($('#nova').value !== $('#nova2').value) {
      msg.className = 'msg ruim'; msg.textContent = 'as duas senhas não são iguais'; return;
    }
    try {
      await api('/api/sessao/redefinir', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({token: token, senha: $('#nova').value})});
      msg.className = 'msg ok';
      msg.textContent = 'senha alterada. Entre com a nova senha.';
      setTimeout(() => { location.hash = '#/painel'; }, 900);
    } catch (e) {
      msg.className = 'msg ruim'; msg.textContent = e.message;
    }
  });
}

function telaConfig() {
  if (ROTA.cfg === 'senha') return telaTrocarSenha();
  if (ROTA.cfg === 'conta') return telaMinhaConta();
  if (ROTA.cfg === 'usuarios') return telaUsuarios();
  // Vínculos é um catálogo, mas aberto por Configurações: reaproveita a
  // lista comum em vez de duplicar a tela inteira.
  if (ROTA.cfg === 'vinculos') return telaLista('vinculo');
  return telaAtualizacoes();
}

/* ----------------------------------------------------------------- senha */
function telaTrocarSenha() {
  const u = ESTADO.usuario || {};
  $('#tela').innerHTML = `
    <h1>Configurações · Trocar senha</h1>
    <p class="nota">As alterações são gravadas com o nome da sua conta
    (<b>${esc(u.nome_completo || '')}</b>). Depois de trocar, as outras sessões
    desta conta saem do ar.</p>
    <p class="msg" id="msg-senha"></p>
    <div class="caixa">
      <label class="campo">Senha atual
        <input type="password" id="atual" autocomplete="current-password"></label>
      <label class="campo">Nova senha
        <input type="password" id="nova" autocomplete="new-password"></label>
      <label class="campo">Repetir a nova senha
        <input type="password" id="nova2" autocomplete="new-password"></label>
      <p class="nota">Ao menos 8 caracteres, uma letra e um número.</p>
      <div class="barra" style="margin:12px 0 0">
        <button class="primario" id="trocar">Trocar senha</button>
      </div>
    </div>`;
  $('#trocar').addEventListener('click', async () => {
    const msg = $('#msg-senha');
    if ($('#nova').value !== $('#nova2').value) {
      msg.className = 'msg ruim'; msg.textContent = 'as duas senhas não são iguais'; return;
    }
    try {
      await api('/api/senha', {method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({atual: $('#atual').value, nova: $('#nova').value})});
      msg.className = 'msg ok';
      msg.textContent = 'senha alterada. Entre de novo com a nova senha.';
      setTimeout(() => sair(), 1200);
    } catch (e) {
      msg.className = 'msg ruim'; msg.textContent = e.message;
    }
  });
}

async function sair() {
  try { await api('/api/sessao', {method: 'DELETE'}); } catch (e) { /* já sem sessão */ }
  ESTADO.usuario = null;
  telaLogin();
}

/* ----------------------------------------------------------- minha conta */
function telaMinhaConta() {
  const u = ESTADO.usuario || {};
  $('#tela').innerHTML = `
    <h1>Configurações · Minha conta</h1>
    <p class="nota">Estes dados vêm da conta de acesso ao sistema, não do cadastro
    aeronáutico. Para editar o cadastro de uma pessoa, use a lista de
    <a href="#/organizacao">empresas e fabricantes</a> ou
    <a href="#/usuario">usuários</a>.</p>
    <div class="caixa"><h3>Conta</h3><dl class="kv">
      <dt>Nome completo</dt><dd>${esc(u.nome_completo || '')}</dd>
      <dt>E-mail</dt><dd>${esc(u.email || '')}</dd>
      <dt>Permissão</dt><dd>${esc({
        administrador: 'administrador — tudo, inclusive contas',
        editor: 'editor — consulta e alteração de cadastros',
        consulta: 'consulta — somente leitura'}[u.papel] || u.papel || '')}</dd>
      <dt>Senha inicial pendente</dt><dd>${u.troca_senha_obrigatoria
        ? '<span class="selo s-editado">sim, troque agora</span>' : 'não'}</dd>
    </dl>
    <div class="barra" style="margin:16px 0 0">
      <a class="btn" href="#/configuracoes/senha">Trocar senha</a>
      <button id="sair">Sair</button>
    </div></div>`;
  $('#sair').addEventListener('click', () => sair());
}

/* ------------------------------------------------------------- usuários */
async function telaUsuarios() {
  $('#tela').innerHTML = '<p class="vazio">carregando contas&hellip;</p>';
  let lista;
  try { lista = await api('/api/usuarios'); }
  catch (e) {
    $('#tela').innerHTML = `<h1>Configurações · Usuários</h1>
      <p class="vazio" style="color:var(--erro)">${esc(e.message)}</p>`;
    return;
  }
  const PAPEIS = {administrador: 'Administrador', editor: 'Editor', consulta: 'Consulta'};
  $('#tela').innerHTML = `
    <h1>Configurações · Usuários</h1>
    <p class="nota">Quem pode entrar no sistema. A senha nunca é mostrada: só é
    definida na criação, ou trocada depois por "Trocar senha".</p>
    <p class="msg" id="msg-usuario"></p>
    <div class="caixa">
      <table><thead><tr><th>Nome</th><th>E-mail</th><th>Permissão</th>
        <th>Ativo</th><th>Último acesso</th><th></th></tr></thead><tbody>
        ${lista.map(u => `<tr data-uid="${u.id}">
          <td>${esc(u.nome_completo)}</td>
          <td>${esc(u.email)}</td>
          <td>${esc(PAPEIS[u.papel] || u.papel)}</td>
          <td>${u.ativo ? 'sim' : '<span class="selo s-vazio">não</span>'}</td>
          <td>${esc(u.ultimo_acesso_em || '—')}</td>
          <td class="num"><button data-acao="alternar" data-uid="${u.id}">
            ${u.ativo ? 'Desativar' : 'Ativar'}</button>
            <button data-acao="excluir" data-uid="${u.id}">Excluir</button></td>
        </tr>`).join('')}
      </tbody></table>
    </div>
    <div class="caixa">
      <h3>Nova conta</h3>
      <p class="nota">A senha segue a política: 8 caracteres, uma letra e um número.</p>
      <label class="campo">Nome completo
        <input type="text" id="u-nome"></label>
      <label class="campo">E-mail
        <input type="email" id="u-email"></label>
      <label class="campo">Senha
        <input type="text" id="u-senha" autocomplete="off"></label>
      <label class="campo">Permissão
        <select id="u-papel">
          <option value="consulta">consulta — só ler</option>
          <option value="editor">editor — ler e alterar</option>
          <option value="administrador">administrador — tudo</option>
        </select></label>
      <div class="barra" style="margin:12px 0 0">
        <button class="primario" id="u-criar">Criar conta</button>
      </div>
    </div>`;

  const msg = $('#msg-usuario');
  $('#u-criar').addEventListener('click', async () => {
    try {
      const novo = await api('/api/usuarios', {method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          nome_completo: $('#u-nome').value.trim(),
          email: $('#u-email').value.trim(),
          senha: $('#u-senha').value,
          papel: $('#u-papel').value})});
      msg.className = 'msg ok';
      msg.textContent = `conta ${novo.email} criada`;
      await telaUsuarios();
    } catch (e) {
      msg.className = 'msg ruim'; msg.textContent = e.message;
    }
  });

  for (const b of $$('#tela button[data-acao]')) {
    b.addEventListener('click', async () => {
      const uid = Number(b.dataset.uid);
      const acao = b.dataset.acao;
      try {
        if (acao === 'alternar') {
          const alvo = lista.find(u => u.id === uid);
          await api('/api/usuarios/' + uid, {method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ativo: !alvo.ativo})});
          msg.className = 'msg ok';
          msg.textContent = 'conta atualizada';
        } else {
          const alvo = lista.find(u => u.id === uid);
          if (!confirm(`Excluir a conta ${alvo.email}?`)) return;
          await api('/api/usuarios/' + uid, {method: 'DELETE'});
          msg.className = 'msg ok';
          msg.textContent = `conta ${alvo.email} excluída`;
        }
        await telaUsuarios();
      } catch (e) {
        msg.className = 'msg ruim'; msg.textContent = e.message;
      }
    });
  }
}

function contagem(s) {
  if (!s || s.faltam_minutos == null) return 'aguardando primeira verificação';
  const min = s.faltam_minutos;
  if (min <= 0) return `vencido há ${Math.abs(min)} min`;
  const d = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60);
  return `faltam ${d ? `${d}d ` : ''}${h ? `${h}h ` : ''}${min % 60}min`;
}

/* A contagem é uma subtração a partir do carimbo gravado no banco, recalculada
   de tempos em tempos. Ela não depende do relógio do servidor: se o navegador
   ficou aberto a noite toda, o número cai junto com o tempo real. */
function iniciarContagem() {
  if (ESTADO.timer) clearInterval(ESTADO.timer);
  ESTADO.timer = setInterval(async () => {
    try {
      const s = await api('/api/atualizacao/estado');
      ESTADO.atualizacao = s;
      pintarEstado();
    } catch (e) {
      if (e.status === 401) { ESTADO.usuario = null; telaLogin(); }
      /* servidor fora do ar: a tela para de contar, não quebra */
    }
  }, 20000);
}

async function gravarCfg(dados) {
  const msg = $('#msg-cfg');
  msg.className = 'msg';
  msg.textContent = 'gravando…';
  try {
    const s = await api('/api/atualizacao/config',
      {method: 'PUT', headers: {'Content-Type': 'application/json'},
       body: JSON.stringify(dados)});
    ESTADO.atualizacao = s;
    pintarMenu();
    msg.className = 'msg ok';
    msg.textContent = `Salvo. Próxima verificação em ${contagem(s)}.`;
    const luz = $('.sema .luz');
    if (luz) luz.className = 'luz ' + s.cor;
  } catch (e) {
    msg.className = 'msg ruim';
    msg.textContent = e.message;
  }
}

async function executarAgora() {
  const msg = $('#msg-cfg');
  const luz = $('.sema .luz');
  msg.className = 'msg';
  msg.textContent = 'procurando a pasta mais recente e comparando…';
  if (luz) luz.className = 'luz AMARELO';
  try {
    const r = await api('/api/atualizacao/executar', {method: 'POST'});
    msg.className = 'msg ok';
    msg.textContent = r.mensagem || 'concluído';
    await telaAtualizacoes();
  } catch (e) {
    msg.className = 'msg ruim';
    msg.textContent = e.message;
  }
}

async function carregarHistorico() {
  const alvo = $('#historico');
  if (!alvo) return;
  let lista;
  try { lista = await api('/api/atualizacao/historico'); }
  catch (e) { alvo.innerHTML = `<p class="vazio">${esc(e.message)}</p>`; return; }
  if (!lista.length) {
    alvo.innerHTML = '<p class="vazio">Nenhuma verificação ainda.</p>';
    return;
  }
  alvo.innerHTML = lista.map(x => `
    <div class="caixa" style="margin-bottom:11px">
      <div class="barra" style="margin:0">
        <b>${esc(x.pasta || 'sem pasta')}</b>
        <span class="selo ${x.estado === 'ok' ? 's-ok'
          : x.estado === 'erro' ? 's-erro' : 's-na'}">${esc(x.estado)}</span>
        <small class="nota" style="margin:0">${esc(x.origem)} · ${esc(x.iniciada_em)}
          ${x.duracao_s != null ? '· ' + dec(x.duracao_s, 1) + 's' : ''}</small>
      </div>
      <p class="nota" style="margin:6px 0 0">${esc(x.mensagem || '')}</p>
      ${x.diferencas && x.diferencas.length ? `
        <h3>Diferenças gravadas (${x.diferencas.length})</h3>
        <table class="diff"><thead><tr><th>Tabela</th><th>Registro</th><th>Campo</th>
          <th>Antes</th><th>Depois</th><th>Origem</th></tr></thead><tbody>
        ${x.diferencas.map(df => `<tr>
          <td>${esc(df.tabela)}</td>
          <td>${esc(df.rotulo || df.chave)}</td>
          <td><b>${esc(df.campo)}</b></td>
          <td class="antes">${esc(df.antes == null ? 'vazio' : df.antes)}</td>
          <td class="depois">${esc(df.depois == null ? 'vazio' : df.depois)}</td>
          <td><small>${esc(df.origem || '')}</small></td></tr>`).join('')}
        </tbody></table>` : ''}
    </div>`).join('');
}

async function carregarFontes() {
  const alvo = $('#lista-fontes');
  if (!alvo) return;
  let lista;
  try { lista = await api('/api/fontes'); }
  catch (e) { alvo.innerHTML = `<p class="vazio">${esc(e.message)}</p>`; return; }
  if (!lista.length) {
    alvo.innerHTML = '<p class="vazio">Nenhum link cadastrado.</p>';
    return;
  }
  const selo = s => s === 'convertido' ? '<span class="selo s-ok">convertido em .md</span>'
    : s === 'inalterado' ? '<span class="selo s-na">sem mudança</span>'
    : s === 'sem_conversao' ? '<span class="selo s-lock">não convertido</span>'
    : s === 'erro' ? '<span class="selo s-erro">erro</span>'
    : '<span class="selo s-na">pendente</span>';
  alvo.innerHTML = `<table><thead><tr><th>Título</th><th>Formato</th><th>Estado</th>
    <th class="num">Tamanho</th><th>Coletado em</th><th></th></tr></thead><tbody>`
    + lista.map(f => `<tr>
        <td><b>${esc(f.titulo)}</b><br><small class="nota" style="margin:0">${esc(f.url)}</small>
          ${f.erro ? `<br><small style="color:var(--warn)">${esc(f.erro)}</small>` : ''}</td>
        <td>.${esc(f.formato_arquivo || f.formato_declarado)}</td>
        <td>${selo(f.status)}</td>
        <td class="num">${f.tamanho_bytes ? nf(f.tamanho_bytes) + ' B' : '—'}</td>
        <td><small>${esc(f.coletado_em || '—')}</small></td>
        <td><button class="perigo" data-apaga-fonte="${f.id}">×</button>
          <button data-ver-fonte="${f.id}">ver</button></td></tr>
      ${f.status === 'convertido' ? `<tr><td colspan="6">
        <details><summary>documento convertido</summary>
        <pre class="doc">${esc((f.documento_md || '').slice(0, 40000))}</pre></details>
      </td></tr>` : ''}`).join('')
    + `</tbody></table>`;
  $$('[data-apaga-fonte]').forEach(b => b.addEventListener('click', async () => {
    if (!confirm('Apagar este link?')) return;
    try {
      await api(`/api/fontes/${b.dataset.apagaFonte}`, {method: 'DELETE'});
      await carregarFontes();
    } catch (e) { msgFonte('ruim', e.message); }
  }));
  $$('[data-ver-fonte]').forEach(b => b.addEventListener('click', async () => {
    try {
      const doc = await api(`/api/fontes/${b.dataset.verFonte}`);
      const d = doc.documento_md;
      alvo.insertAdjacentHTML('afterbegin', `<div class="caixa">
        <div class="barra"><b>${esc(doc.titulo)}</b>
        <button onclick="this.closest('.caixa').remove()">fechar</button></div>
        ${d ? `<pre class="doc">${esc(d.slice(0, 40000))}</pre>`
            : `<p class="nota">${esc(doc.erro || 'sem documento convertido')}</p>`}
      </div>`);
      window.scrollTo({top: 0, behavior: 'smooth'});
    } catch (e) { msgFonte('ruim', e.message); }
  }));
}

function msgFonte(classe, texto) {
  const el = $('#msg-fonte');
  if (!el) return;
  el.className = 'msg ' + classe;
  el.textContent = texto;
}

async function adicionarFonte() {
  const url = $('#f-url').value.trim();
  if (!url) { msgFonte('ruim', 'informe a URL'); return; }
  msgFonte('', 'baixando e convertendo…');
  try {
    const r = await api('/api/fontes', {method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({url: url, titulo: $('#f-titulo').value.trim(),
                            formato: $('#f-formato').value})});
    msgFonte('ok', `cadastrado: ${r.status}`
      + (r.erro ? ` — ${r.erro}` : '')
      + (r.bytes ? ` · ${nf(r.bytes)} bytes` : ''));
    $('#f-url').value = ''; $('#f-titulo').value = '';
    await carregarFontes();
  } catch (e) { msgFonte('ruim', e.message); }
}

/* --------------------------------------------------------------- arranque */
async function carregarContagens() {
  try {
    const c = await api('/api/catalogo/contagens');
    ESTADO.contagens = Object.fromEntries(
      (Array.isArray(c) ? c : c.contagens || []).map(x => [x.catalogo, x.n]));
  } catch (e) { /* contador do menu é cortesia, não é carga da tela */ }
  try {
    ESTADO.atualizacao = await api('/api/atualizacao/estado');
  } catch (e) { /* servidor sem a camada ainda */ }
}

/* O arranque pergunta quem é o usuário **antes** de desenhar qualquer rota.
   Sem isso, a tela pediria uma lista de cadastros para responder 401 e
   mostraria o erro no lugar do formulário de login. */
async function arrancar() {
  document.body.classList.remove('sem-sessao');
  $('#acesso').style.display = 'none';
  $('#app').style.display = '';
  if (!ESTADO.usuario) {
    try { ESTADO.usuario = await api('/api/sessao'); }
    catch (e) { ESTADO.usuario = null; }
  }
  ESTADO.sessaoResolvida = true;
  if (!ESTADO.usuario) return telaLogin();
  await carregarContagens();
  pintarMenu();
  await render();
  iniciarContagem();
}

function ligarTopo() {
  $('#colapsar').addEventListener('click', () => {
    ESTADO.colapsado = !ESTADO.colapsado;
    $('#app').classList.toggle('colapsada', ESTADO.colapsado);
    $('#colapsar').classList.toggle('on', ESTADO.colapsado);
    $('#colapsar').title = ESTADO.colapsado ? 'Expandir o menu' : 'Colapsar o menu';
    localStorage.setItem('anac-colapsado', ESTADO.colapsado ? '1' : '');
  });
  const busca = $('#busca-menu');
  busca.addEventListener('input', () => {
    ESTADO.filtroMenu = busca.value;
    pintarMenu();
  });
  $('#btn-usuario').addEventListener('click',
    () => ir({tela: 'config', cfg: 'conta'}));
  ESTADO.colapsado = localStorage.getItem('anac-colapsado') === '1';
  $('#app').classList.toggle('colapsada', ESTADO.colapsado);
  $('#colapsar').classList.toggle('on', ESTADO.colapsado);
}

window.addEventListener('hashchange', () => {
  if (!ESTADO.usuario) return;
  render();
});
window.addEventListener('error', ev => {
  const el = $('#msg');
  if (el && ev.message) { el.className = 'msg ruim'; el.textContent = ev.message; }
});
ligarTopo();
arrancar();
</script>
</body>
</html>
"""
