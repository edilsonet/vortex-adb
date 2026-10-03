"""Casca HTML do painel: estilos e o JavaScript que desenha os gráficos.

Separado de `build_dashboard.py` para manter cada arquivo legível. O placeholder
`__DADOS__` é substituído pelo JSON com o resultado das consultas.

Sem CDN e sem npm: o arquivo abre com duplo clique e funciona offline.
"""

HTML_HEAD = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ANAC · Registro Aeronáutico Brasileiro</title>
<style>
:root{
  --bg:#0d1117; --card:#161b22; --line:#21262d; --txt:#e6edf3; --dim:#8b949e;
  --a:#58a6ff; --b:#3fb950; --c:#f0883e; --d:#db6d28; --e:#a371f7; --warn:#d29922;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:26px 30px 16px;border-bottom:1px solid var(--line)}
h1{margin:0 0 5px;font-size:21px;font-weight:650;letter-spacing:-.2px}
.sub{color:var(--dim);font-size:13px}
main{padding:20px 30px 60px;max-width:1500px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:11px;margin-bottom:26px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:13px 15px}
.kpi b{display:block;font-size:22px;font-weight:660;letter-spacing:-.4px;margin-bottom:2px}
.kpi span{color:var(--dim);font-size:11.5px;text-transform:uppercase;letter-spacing:.4px}
/* Elementos que abrem um cadastro. O painel virou uma porta de entrada para o
   registro, entao precisa parecer clicavel sem depender do cursor: a borda muda
   de cor e o fundo acende no hover, tanto no KPI quanto no nome da tabela. */
.kpi.link{display:block;text-decoration:none;color:inherit;transition:border-color .12s,background .12s}
.kpi.link:hover{border-color:var(--a);background:#1a2331}
.kpi.link::after{content:'92';float:right;color:var(--dim);font-size:12px;opacity:0;transition:opacity .12s}
.kpi.link:hover::after{opacity:1}
a.reg{color:var(--a);text-decoration:none;border-bottom:1px dotted #1f6feb77}
a.reg:hover{color:#79c0ff;border-bottom-style:solid}
a.reg::after{content:'97';font-size:9px;vertical-align:super;opacity:.55;margin-left:1px}
section{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:19px 21px;margin-bottom:17px}
h2{margin:0 0 4px;font-size:15.5px;font-weight:620}
.note{color:var(--dim);font-size:12.5px;margin:0 0 15px;max-width:82ch}
.h3{font-size:13.5px;font-weight:600;margin:0 0 8px}
.cite{color:var(--warn);font-family:ui-monospace,Consolas,monospace;font-size:11.5px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(390px,1fr));gap:22px}
table{width:100%;border-collapse:collapse;font-size:12.8px}
th{text-align:left;color:var(--dim);font-weight:560;padding:6px 9px;
  border-bottom:1px solid var(--line);font-size:11.5px;text-transform:uppercase;letter-spacing:.3px}
td{padding:5px 9px;border-bottom:1px solid #1c2128}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:5px;background:var(--a);border-radius:3px;min-width:2px}
.tag{display:inline-block;padding:1px 7px;border-radius:11px;font-size:10.5px;font-weight:600}
.t121{background:#1f6feb33;color:#58a6ff}.t135{background:#2ea04333;color:#3fb950}
.tna{background:#30363d;color:#8b949e}
.legend{display:flex;gap:15px;flex-wrap:wrap;color:var(--dim);font-size:12px;margin-top:11px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
svg{overflow:visible;display:block}
.warnbox{background:#3d1d0d55;border:1px solid #9e6a03;border-radius:8px;padding:12px 15px;margin-bottom:17px}
.warnbox b{color:var(--warn)}
footer{color:var(--dim);font-size:12px;padding:20px 30px;border-top:1px solid var(--line)}
</style>
</head>
<body>
<header>
  <h1>Registro Aeronáutico Brasileiro · painel</h1>
  <div class="sub" id="cabecalho"></div>
</header>
<main>
  <div class="warnbox">
    <b>CPF não é identificador aqui.</b> A ANAC publica CPF mascarado
    (<span class="cite">062.XXX.XXX-89</span> no RAB, <span class="cite">****525.372***</span> no SISANT).
    O modelo separa pessoa física de jurídica pelo formato, mas a identidade da
    pessoa física é nome + UF. Trate como dado de análise, não como cadastro de
    pessoas.
  </div>
  <div class="kpis" id="kpis"></div>
  __SECOES__
</main>
<footer id="rodape"></footer>
<script>
const D = __DADOS__;
</script>
<script>
__JS__
</script>
</body>
</html>
"""
