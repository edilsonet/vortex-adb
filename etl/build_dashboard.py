"""Gera o painel HTML autocontido.

Roda as consultas de `analytics`, monta as seções e injeta tudo num único
arquivo, sem CDN e sem npm: o painel abre com duplo clique e funciona offline.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import analytics as A
import concentracao as C
from common import BUILD, DB_PATH
from dashboard_html import HTML_HEAD
from dashboard_js import JS

SAIDA = BUILD / "dashboard.html"
CACHE_CONC = BUILD / "concentracao.json"


def _concentracao(conn) -> dict:
    """Índices de concentração, com cache em disco.

    A agregação roda em Python sobre 865 mil vínculos e leva cerca de dois
    minutos. O painel é reconstruído com frequência e a resposta não muda
    entre reconstruções, então o resultado fica num JSON ao lado do banco,
    invalidado quando o banco é mais novo.
    """
    import json as _json
    if CACHE_CONC.exists() and CACHE_CONC.stat().st_mtime >= DB_PATH.stat().st_mtime:
        try:
            return _json.loads(CACHE_CONC.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    print("  índices de concentração (~2 min)…")
    saida = {
        "analise": C.analise(conn),
        "serie": C.serie_mensal(conn),
    }
    CACHE_CONC.write_text(_json.dumps(saida, ensure_ascii=False, default=str),
                         encoding="utf-8")
    return saida


def n(v) -> str:
    """Número no formato pt-BR, com ponto de milhar."""
    return f"{v:,}".replace(",", ".")


def _sec(titulo, nota, corpo, cid=""):
    ident = f' id="{cid}"' if cid else ""
    return (f'<section{ident}><h2>{titulo}</h2><p class="note">{nota}</p>'
            f'{corpo}</section>')


def montar_secoes(conc: dict) -> str:
    s = []

    s.append(_sec(
        "1 · Evolução da frota",
        "Doze snapshots mensais do RAB. A cor do ponto marca o formato de "
        "exportação daquele mês — a ANAC trocou o layout duas vezes na série, e "
        "cada trecho da linha foi normalizado para o mesmo modelo antes de ser "
        "somado.",
        '<div id="g-frota"></div>'))

    s.append(_sec(
        "2 · Quem é dono da frota brasileira",
        "A intuição de que o dono é a companhia aérea não se sustenta: o topo "
        "da lista inclui braços de arrendamento mercantil, bancos e o próprio "
        "fabricante. É o resultado da titularização — a aeronave fica no "
        "patrimônio do credor e a empresa aérea aparece como operador.",
        '<div id="hhi" style="margin-bottom:12px"></div>'
        '<div id="t-prop"></div>'
        '<p class="note" style="margin-top:11px">HHI baixo indica frota '
        'dispersa: o índice soma a fatia de cada proprietário ao quadrado. '
        'O maior detém pouco mais de 2% — a cauda longa é a característica, '
        'não a exceção.</p>'))

    ca = conc["analise"]
    serie = conc["serie"]
    h0, h1 = serie[0], serie[-1]
    metade = ca["metade_da_frota_em"]
    pct_metade = metade / ca["grupos"] * 100

    s.append(_sec(
        "3 · Concentração de propriedade e cauda de securitização",
        "Três medidas, porque uma só esconde o essencial. O <b>HHI por frota</b> "
        "soma ao quadrado a fatia de cada dono sobre o total de aeronaves — é a "
        "única base que existe nos doze meses, já que a era A do RAB não traz "
        "percentual de propriedade. O <b>HHI de crédito</b> tira desse total "
        "governo e fabricante: as aeronaves que a EMBRAER guarda e as que o DAC "
        "detém não competem no mercado de aquisição financiada, e somá-las mede "
        "integração vertical em vez de concorrência. O <b>HHI declarado</b> usa "
        f"o percentual que a fonte traz e cobre {ca['indices']['cobertura_pct']}% "
        "dos vínculos do mês.",
        '<div id="conc-kpis" class="kpis" style="margin-bottom:18px"></div>'
        '<div class="grid2">'
        '<div><h3 class="h3">HHI mês a mês</h3>'
        '<div id="g-hhi-serie"></div>'
        f'<p class="note" style="margin-top:9px">Os dois índices caem no '
        f'período: de {h0["hhi_frota"]:.2f} para {h1["hhi_frota"]:.2f} na frota '
        f'e de {h0["hhi_credito"]:.2f} para {h1["hhi_credito"]:.2f} no crédito. '
        'Uma leitura que somasse percentual declarado mostraria subida no mesmo '
        'intervalo, mas ela mediria a troca de formato da fonte, não '
        'concentração. Escala FTC: abaixo de 1.500 pontos o mercado é '
        'desconcentrado.</p></div>'
        '<div><h3 class="h3">Curva de Lorenz</h3>'
        '<div id="g-lorenz"></div>'
        f'<p class="note" style="margin-top:9px">Cada ponto é um dono, do maior '
        f'para o menor. Metade das {n(ca["frota_com_dono"])} aeronaves do mês '
        f'está em {n(metade)} dos {n(ca["grupos"])} donos — {pct_metade:.0f}% '
        f'deles. A distância entre a curva e a diagonal é o Gini, '
        f'{ca["indices"]["gini"]:.3f}.</p></div>'
        '</div>'
        '<div class="grid2" style="margin-top:22px">'
        '<div><h3 class="h3">Perfil da frota por segmento de dono</h3>'
        '<div id="g-segmento"></div></div>'
        '<div><h3 class="h3">A cauda de titularização, por pool</h3>'
        '<div id="t-pools"></div></div></div>'
        '<h3 class="h3" style="margin:24px 0 6px">Maiores veículos de '
        'securitização e arrendamento</h3>'
        '<p class="note">Entidades que são proprietárias de aeronaves e não '
        'operam nenhuma delas. É a assinatura verificável de titularização: a '
        'aeronave fica no patrimônio do credor e a companhia aérea aparece como '
        'operadora em outro registro. O teste é por aeronave — uma locadora que '
        'também opera três nao é tratada como operadora das outras 380.</p>'
        '<div id="t-titulares"></div>'
        '<h3 class="h3" style="margin:24px 0 6px">Fragmentação de identidade por '
        'grafia</h3>'
        '<p class="note">A fonte escreve o mesmo dono de várias maneiras. O '
        'HHI sai nas duas bases — nome normalizado e conjunto de radicais — '
        'para que a diferença entre elas fique visível em vez de escolhida em '
        'silêncio.</p>'
        '<div id="t-fragmentacao"></div>'))

    s.append(_sec(
        "4 · Autorizações 121 e 135",
        "Operadores com operação registrada sob o RBAC 121 (serviço público "
        "regular) ou RBAC 135 (não regular). O vínculo é por aeronave, e o "
        "flag vem do próprio registro da ANAC.",
        '<p class="note">Base normativa: <span class="cite">RBAC 45.12-I(a)</span> — '
        '"Uma pessoa somente pode operar uma aeronave em operações segundo o '
        'RBAC nº 135 se na aeronave estiver colocada a inscrição TRANSPORTE '
        'PÚBLICO".</p>'
        '<div id="t-auth"></div>'))

    s.append(_sec(
        "5 · Aeródromos",
        "Aeródromos públicos, privados, helipontos e helidecks, por unidade "
        "federativa.",
        '<div class="grid2"><div id="g-aero"></div>'
        '<div id="t-aero"></div></div>'))

    s.append(_sec(
        "6 · Fabricantes, modelos e idade da frota",
        "O fabricante é modelado como empresa, conforme o grafo, e vem do campo "
        "<span class=\"cite\">NM_FABRICANTE</span>. A classificação por classe usa "
        "<span class=\"cite\">CD_CLS</span> e a década vem de "
        "<span class=\"cite\">NR_ANO_FABRICACAO</span>.",
        '<div class="grid2"><div id="t-fab"></div>'
        '<div><div id="g-classe"></div><div id="g-decada" style="margin-top:14px">'
        '</div></div></div>'
        '<div id="t-modelos" style="margin-top:16px"></div>'))

    s.append(_sec(
        "7 · Drones e aeronaves de pequeno porte (SISANT)",
        "Registro do SISANT, separado por ramo de atividade. É a fonte com maior "
        "volume e a que mais sofre com CPF mascarado.",
        '<div id="sisant-split" class="note" style="margin-bottom:12px"></div>'
        '<div class="grid2"><div id="g-sisant"></div><div></div></div>'))

    s.append(_sec(
        "8 · Rede de participações",
        "Entidades com maior número de vínculos no snapshot corrente. Cada "
        "vínculo é uma relação pessoa–aeronave com participação fracionária, "
        "versionada por mês.",
        '<div id="t-rede"></div>'))

    s.append(_sec(
        "9 · Qualidade e cobertura",
        "O que a fonte não entrega. Ler antes de usar o painel para decidir: "
        "metade dos vínculos não tem documento, e o CPF nunca vem completo.",
        '<div id="t-qual"></div>'))

    s.append(_sec(
        "10 · REDEMET — situação meteorológica",
        "Coleta da API da DECEA. O status por cor segue a tabela da API: "
        "<b>g</b> verde, <b>gw</b> verde com aviso, <b>y</b> amarelo, "
        "<b>yw</b> amarelo com aviso, <b>cinza</b> sem METAR disponível. "
        "A coleta é limitada por padrão — a API impõe limite de uso e consultar "
        "as 6.139 localidades do cadastro seria abusivo.",
        '<div id="redemet-cabecalho" class="note" style="margin-bottom:12px"></div>'
        '<div class="grid2"><div id="g-redemet"></div>'
        '<div><div id="t-metar"></div></div></div>'
        '<div id="t-taf" style="margin-top:16px"></div>'))

    return "\n".join(s)


def coletar(conn) -> dict:
    return {
        "resumo": A.resumo_geral(conn),
        "frota": A.frota_mensal(conn),
        "propriedade": A.concentracao_propriedade(conn),
        "hhi": A.hhi_propriedade(conn),
        "autorizacoes": A.mix_autorizacoes(conn),
        "auth_totais": A.totais_autorizacoes(conn),
        "aero_tipo": A.aerodromos_por_tipo(conn),
        "aero_uf": A.aerodromos_por_uf(conn),
        "aero_publicos": A.aerodromos_publicos(conn),
        "marcas": A.frota_por_marca(conn),
        "classes": A.frota_por_classe(conn),
        "decadas": A.frota_por_decada(conn),
        "fabricantes": A.fabricantes_top(conn),
        "modelos": A.modelos_top(conn),
        "sisant_ramo": A.sisant_por_ramo(conn),
        "sisant_split": A.sisant_split(conn),
        "rede": A.rede_participacao(conn),
        "redemet": A.redemet_resumo(conn),
        "redemet_cores": A.redemet_status_por_cor(conn),
        "redemet_metar": A.redemet_mensagens(conn, "METAR", limite=8),
        "redemet_taf": A.redemet_mensagens(conn, "TAF", limite=5),
        "qualidade": A.qualidade(conn),
        "concentracao": _concentracao(conn),
    }


def main() -> int:
    if not DB_PATH.exists():
        print(f"banco ausente: {DB_PATH} (rode etl/run.py antes)")
        return 2
    conn = sqlite3.connect(DB_PATH)
    dados = coletar(conn)
    conn.close()

    html = (HTML_HEAD
            .replace("__SECOES__", montar_secoes(dados["concentracao"]))
            .replace("__DADOS__", json.dumps(dados, ensure_ascii=False,
                                             default=str))
            .replace("__JS__", JS))
    BUILD.mkdir(parents=True, exist_ok=True)
    SAIDA.write_text(html, encoding="utf-8")

    print(f"painel: {SAIDA}  ({len(html) / 1024:.0f} KB)")
    print(f"seções: {html.count('<h2>')} | snapshots: {len(dados['frota'])} | "
          f"violações de FK: {dados['qualidade']['violacoes_fk']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
