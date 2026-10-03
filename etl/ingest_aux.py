"""Carga das fontes auxiliares: aeródromos, SISANT, produtos, peças e produção.

Cada arquivo tem convenção de cabeçalho própria (o latin-1 dos aeródromos
públicos versus o UTF-8-BOM do RAB), então o acesso é por nome de coluna
declarado aqui, nunca por posição.
"""

from __future__ import annotations

import json
import sqlite3

from common import FONTE, read_delimited
from db import CatalogoRepo, PessoaRepo, log_ingestao

# (arquivo, tipo, coluna de código, colunas de nome)
FONTES_AERODROMO = [
    ("AerodromosPublicos.csv", "PUBLICO",   "Código OACI", "Nome"),
    ("AerodromosPrivados.csv", "PRIVADO",   "Código OACI", "Nome"),
    ("Helipontos.csv",         "HELIPONTO", "Código OACI", "Nome"),
    ("Helidecks.csv",          "HELIDECK",  "Código OACI", "Nome"),
]


def _get(reg: dict, *nomes):
    for n in nomes:
        if n in reg and reg[n] not in (None, ""):
            return reg[n]
    return None


def _float(valor):
    if valor in (None, ""):
        return None
    try:
        return float(str(valor).replace(",", "."))
    except ValueError:
        return None


def carregar_aerodromos(conn: sqlite3.Connection) -> dict:
    """Os quatro arquivos de aeródromos caem na mesma tabela, distinguidos por `tipo`."""
    contagem = {}
    # Nem todo arquivo declara o mesmo conjunto de colunas; cada um traz o seu.
    aliases = {
        "icao": ("Código OACI", "Codigo OACI", "ICAO", "CODIGO_OACI"),
        "ciad": ("CIAD",),
        "nome": ("Nome", "Nome do Aeródromo"),
        "municipio": ("Município", "Municipio"),
        "uf": ("UF", "Sigla", "UF_AERODROMO"),
        "municipio_servido": ("Município Servido", "Municipio Servido"),
        "uf_servido": ("UF Servido",),
        "lat": ("LATGEOPOINT", "Latitude"),
        "lon": ("LONGEOPOINT", "Longitude"),
        "latitude": ("Latitude",),
        "longitude": ("Longitude",),
        "altitude": ("Altitude",),
        "operacao_diurna": ("Operação Diurna", "Operacao Diurna"),
        "operacao_noturna": ("Operação Noturna", "Operacao Noturna"),
        "situacao": ("Situação", "Situacao", "SITUACAO"),
        "validade_registro": ("Validade do Registro", "Validade do Cadastro",
                              "VALIDADE DO CADASTRO"),
    }

    for arquivo, tipo, col_icao, col_nome in FONTES_AERODROMO:
        caminho = FONTE / arquivo
        if not caminho.exists():
            log_ingestao(conn, "AERODROMO", arquivo, 0, "ausente", "")
            contagem[tipo] = contagem.get(tipo, 0)
            continue
        cabecalho, linhas = read_delimited(caminho)
        # Normaliza o cabeçalho: um mesmo campo aparece como `Código OACI` nos
        # aeródromos públicos e `CÓDIGO OACI` nos helidecks.
        from common import normaliza_nome as _norm
        idx = {_norm(c): i for i, c in enumerate(cabecalho)}

        def val(reg_i, campo):
            for alias in aliases[campo]:
                j = idx.get(_norm(alias))
                if j is not None and j < len(linhas[reg_i]) and linhas[reg_i][j].strip():
                    return linhas[reg_i][j].strip()
            return None

        inseridos = 0
        for i in range(len(linhas)):
            icao = (val(i, "icao") or "").upper()
            if not icao:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO aerodromo (icao, ciad, nome, tipo, lat, lon, "
                "latitude, longitude, altitude, municipio, uf, municipio_servido, "
                "uf_servido, operacao_diurna, operacao_noturna, situacao, validade_registro) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (icao, val(i, "ciad"), val(i, "nome"), tipo,
                 _float(val(i, "lat")), _float(val(i, "lon")),
                 val(i, "latitude"), val(i, "longitude"), val(i, "altitude"),
                 val(i, "municipio"), val(i, "uf"), val(i, "municipio_servido"),
                 val(i, "uf_servido"), val(i, "operacao_diurna"),
                 val(i, "operacao_noturna"), val(i, "situacao"), val(i, "validade_registro")),
            )
            inseridos += 1
        contagem[tipo] = inseridos
        log_ingestao(conn, "AERODROMO", arquivo, inseridos, "ok", f"tipo={tipo}")
        print(f"  {arquivo:34} {inseridos:>6} aerodromos ({tipo})")
    return contagem


def carregar_sisant(conn: sqlite3.Connection, pessoas: PessoaRepo) -> int:
    """SISANT: registro de drones e aeronaves de pequeno porte.

    O CPF chega mascarado na origem. `classifica_documento` reconhece o rótulo e
    marca `documento_mascarado`, então a pessoa é criada como usuário com
    identidade por nome+UF — o melhor que a fonte permite.
    """
    caminho = FONTE / "SISANT.csv"
    if not caminho.exists():
        return 0
    cabecalho, linhas = read_delimited(caminho)
    idx = {c: i for i, c in enumerate(cabecalho)}
    lote = []
    for reg in linhas:
        def v(nome):
            j = idx.get(nome)
            return reg[j].strip() if j is not None and j < len(reg) else None
        codigo = v("CODIGO_AERONAVE")
        if not codigo:
            continue
        pid = pessoas.resolver(v("OPERADOR"), v("CPF_CNPJ"), None)
        lote.append((codigo, v("DATA_VALIDADE"), pid, v("TIPO_USO"),
                     v("FABRICANTE"), v("MODELO"), v("NUMERO_SERIE"),
                     _float(v("PESO_MAXIMO_DECOLAGEM_KG")), v("RAMO_ATIVIDADE")))
        if len(lote) >= 20000:
            pessoas.flush()
            conn.executemany(
                "INSERT OR REPLACE INTO registro_sisant (codigo_aeronave, data_validade, "
                "pessoa_id, tipo_uso, fabricante_nome, modelo_nome, num_serie, "
                "peso_max_kg, ramo_atividade) VALUES (?,?,?,?,?,?,?,?,?)", lote)
            lote.clear()
    if lote:
        pessoas.flush()
        conn.executemany(
            "INSERT OR REPLACE INTO registro_sisant (codigo_aeronave, data_validade, "
            "pessoa_id, tipo_uso, fabricante_nome, modelo_nome, num_serie, "
            "peso_max_kg, ramo_atividade) VALUES (?,?,?,?,?,?,?,?,?)", lote)
    log_ingestao(conn, "SISANT", caminho.name, len(linhas) - 1, "ok", "")
    print(f"  {caminho.name:34} {len(linhas) - 1:>6} registros")
    return len(linhas) - 1


def carregar_produtos_e_pecas(conn: sqlite3.Connection, cat: CatalogoRepo,
                             pessoas: PessoaRepo | None = None) -> None:
    """Produtos aeronáuticos certificados e peças aprovadas, ligados ao fabricante."""
    # `fabricante` referencia `pessoa`, então o catálogo tem de estar gravado
    # antes de inserir quem o referencia.
    if pessoas is not None:
        pessoas.flush()
    cat.flush()
    caminho = FONTE / "ProdutosAeronauticos_Fabricantes.csv"
    if caminho.exists():
        cabecalho, linhas = read_delimited(caminho)
        idx = {c: i for i, c in enumerate(cabecalho)}
        lote = []
        for reg in linhas:
            def v(nome):
                j = idx.get(nome)
                return reg[j].strip() if j is not None and j < len(reg) else None
            codi = v("PROD_CODI")
            if not codi:
                continue
            lote.append((
                codi, v("PROD_NOME"), v("PRODT_DESCR"), v("ORG_CODI"),
                v("ORG_NOME"), v("ORG_NABREV"),
                cat.fabricante(v("ORG_NOME") or v("ORG_NABREV"), v("ORG_CODI"), v("ORG_NABREV")),
            ))
        if lote:
            # O lote referencia fabricantes criados durante a leitura acima.
            pessoas.flush()
            cat.flush()
            conn.executemany(
                "INSERT OR REPLACE INTO produto_aeronautico (prod_codi, prod_nome, "
                "prod_descr, org_codi, org_nome, org_nabrev, fabricante_id) "
                "VALUES (?,?,?,?,?,?,?)", lote)
        log_ingestao(conn, "PRODUTO", caminho.name, len(lote), "ok", "")
        print(f"  {caminho.name:34} {len(lote):>6} produtos")

    caminho = FONTE / "PecasAprovadas.csv"
    if caminho.exists():
        cabecalho, linhas = read_delimited(caminho)
        idx = {c: i for i, c in enumerate(cabecalho)}
        lote = []
        for reg in linhas:
            def v(nome):
                j = idx.get(nome)
                return reg[j].strip() if j is not None and j < len(reg) else None
            lote.append((
                v("PAPP_COD"), v("PAPP_NOME"), v("PAPP_PN"), v("PAPP_MODELO"),
                v("PAPP_MODELOAER"), v("PAPP_TIPO"), v("PAPP_OTP"), v("PAPP_REPOE"),
                v("PAPP_MAPROV"), v("PAPP_AUTORIDADE"), v("APAA_CODI"),
                v("APAA_DATA"), v("APAA_STATUS"), v("ORG_CODI"), v("ORG_NOME"),
                v("ORG_NABREV_FAB_PROD"),
                cat.fabricante(v("ORG_NOME"), v("ORG_CODI"), v("ORG_NABREV_FAB_PROD")),
            ))
        if lote:
            pessoas.flush()
            cat.flush()
            # OR IGNORE pela chave natural (papp_cod, org_codi): sem isso, cada
            # recarga de `--manter` duplica a linha.
            conn.executemany(
                "INSERT OR IGNORE INTO peca_aprovada (papp_cod, papp_nome, papp_pn, papp_modelo, "
                "papp_modeloaer, papp_tipo, papp_otp, papp_repoe, papp_maprov, "
                "papp_autoridade, apaa_codi, apaa_data, apaa_status, org_codi, "
                "org_nome, org_nabrev_fab_prod, fabricante_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", lote)
        log_ingestao(conn, "PECA", caminho.name, len(lote), "ok", "")
        print(f"  {caminho.name:34} {len(lote):>6} pecas aprovadas")


def carregar_org_producao(conn: sqlite3.Connection) -> None:
    """Organizações de produção: traz CNPJ direto, a fonte com melhor identidade PJ."""
    caminho = FONTE / "Organizacoes de Producao.csv"
    if not caminho.exists():
        return
    cabecalho, linhas = read_delimited(caminho)
    idx = {c: i for i, c in enumerate(cabecalho)}
    lote = []
    for reg in linhas:
        def v(nome):
            j = idx.get(nome)
            return reg[j].strip() if j is not None and j < len(reg) else None
        razao = v("Razão Social") or v("Razao Social")
        if not razao:
            continue
        lote.append((v("CNPJ"), razao, v("Nome Fantasia"), v("Número do Certificado"),
                     v("Endereço"), v("Complemento"), v("Cidade"), v("UF"), v("CEP"),
                     v("País"), v("Home Page"), v("Tipo de Organização de Produção")))
    if lote:
        conn.executemany(
            "INSERT OR IGNORE INTO org_producao (cnpj, razao_social, nome_fantasia, certificado, "
            "endereco, complemento, cidade, uf, cep, pais, homepage, tipo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", lote)
    log_ingestao(conn, "ORG_PRODUCAO", caminho.name, len(lote), "ok", "")
    print(f"  {caminho.name:34} {len(lote):>6} organizacoes")
