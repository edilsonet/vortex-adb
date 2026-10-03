r"""Verificação periódica da pasta `update/<yyyymmdd>` e o estado da atualização.

O botão **Atualizar** não recarrega o banco: ele lê a pasta mais recente de
`update/`, compara arquivo a arquivo com o que já está gravado e escreve
**somente o que difere**. É a diferença entre "recarreguei a fonte" e "conferi a
fonte": a primeira reescreve 36 mil aeronaves para descobrir que nenhuma mudou, a
segunda compara e não toca em nada.

Quatro decisões que valem explicar:

**1. A pasta mais recente é a de maior nome.** `20260930` > `20260721` em ordem
lexicográfica, porque `YYYYMMDD` é posicional — não há parse de data a fazer, e
`20260930` ordena depois de `20260815` porque o primeiro caractere já decide.
Uma pasta que não bate com `^\d{8}$` é ignorada em silêncio, e o relatório diz
quais foram ignoradas.

**2. Campo a campo, não registro a registro.** Um registro com 40 campos em que
um mudou gera **uma** linha em `atualizacao_diferenca`, não 40 linhas de
UPDATE. O relatório então responde "o que mudou de verdade" em vez de
"qualquer coisa mudou em algum lugar".

**3. A referência do que já foi aplicado vem do próprio banco.** `pasta_aplicada`
guarda a última pasta concluída. Reexecutar a mesma pasta não repete trabalho:
o hash de cada arquivo fica em `atualizacao_arquivo` e, se nada mudou, a pasta
é marcada `ignorado` com a contagem de campos alterados em zero.

**4. A contagem offline é uma subtração.** `ultima_verificacao_em` fica gravada no
banco; a tela faz `agora - ultima_verificacao_em` e compara com o intervalo.
Ficar três dias com o servidor desligado não zera nada: na volta, a diferença já
estourou o intervalo e a atualização dispara — que é exatamente o que se quer.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import threading
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common import DB_PATH, ROOT, read_delimited, repair_json_anac

ATUALIZACAO = ROOT / "schema" / "atualizacao.sql"
PASTA_UPDATE = ROOT / "update"
INTERVALO_PADRAO = 1440          # minutos (24 h)
INTERVALO_MINIMO = 5
NOME_PASTA = re.compile(r"^\d{8}$")

# Coluna-chave de cada tabela que a pasta `update` pode corrigir. A chave é o
# que a pessoa reconhece: OACI, matrícula, CNPJ, código SISANT. Sem isso, um
# arquivo novo não teria como casar com o registro existente.
CHAVE = {
    "aerodromo": ("icao", ["icao"]),
    "aeronave": ("id", ["nr_cert_matricula", "matricula", "chave_natural", "id"]),
    # `id` vem por último de propósito: documento e chave são a identidade real
    # de uma pessoa e valem mais que um id local. O `id` entra só como último
    # recurso, para o arquivo que traz a chave primária ser reconhecido como
    # atualização e não como tentativa de inserir um cadastro duplicado.
    "pessoa": ("id", ["documento", "cpf_cnpj", "chave", "id"]),
    "org_producao": ("cnpj", ["cnpj"]),
    "registro_sisant": ("codigo_aeronave", ["codigo_aeronave", "codigo"]),
    "marca": ("nome", ["nome", "marca"]),
    "fabricante": ("id", ["org_codigo", "org_nabrev"]),
    "modelo": ("id", ["id", "cd_modelo"]),
}
# Nome de arquivo que declara qual tabela é. Sem o nome, o conteúdo decide:
# `registros_sisant` só tem `codigo_aeronave`, e isso já basta.
TABELA_POR_NOME = {
    "aero": "aerodromo", "aerodromo": "aerodromo", "aeroportos": "aerodromo",
    "aeronave": "aeronave", "aeronaves": "aeronave", "frota": "aeronave",
    "pessoa": "pessoa", "pessoas": "pessoa", "empresa": "pessoa",
    "empresas": "pessoa", "usuario": "pessoa", "usuarios": "pessoa",
    "sisant": "registro_sisant", "drone": "registro_sisant",
    "drones": "registro_sisant",
    "marca": "marca", "marcas": "marca",
    "modelo": "modelo", "modelos": "modelo",
    "fabricante": "fabricante", "fabricantes": "fabricante",
    "org": "org_producao", "organizacoes": "org_producao",
}
_lock = threading.Lock()
_thread: threading.Thread | None = None
_parar = threading.Event()


# ------------------------------------------------------------------ utilidades
# Marcador de "campo não informado", distinto de `None`, que é um valor
# gravável de verdade (`etapa=None` limpa a etapa; `erro=None` limpa o erro).
_AUSENTE = object()


def _agora() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso(momento: datetime | None = None) -> str:
    return (momento or _agora()).isoformat(timespec="seconds")


def _texto(valor) -> str | None:
    if valor is None:
        return None
    s = str(valor).strip()
    return s or None


def _norm(chave: str) -> str:
    """Chave de coluna normalizada: sem acento, minúscula, só alfanumérico.

    O RAB escreve `NR_CERT_MATRICULA`, a lista de aeródromos escreve
    `ICAO`, e quem monta a pasta de atualização vai escrever `icao`, `ICAO` ou
    `Icao`. As três precisam casar com a mesma coluna.
    """
    t = unicodedata.normalize("NFKD", chave or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", t.lower())


def aplicar_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(ATUALIZACAO.read_text(encoding="utf-8"))
    conn.commit()
    _semear(conn)


def _semear(conn: sqlite3.Connection) -> None:
    """Configuração inicial e linha única de estado."""
    for chave, valor in (("intervalo_minutos", str(INTERVALO_PADRAO)),
                         ("auto_ligado", "1"),
                         ("ultima_verificacao_em", ""),
                         ("pasta_aplicada", "")):
        conn.execute("INSERT OR IGNORE INTO atualizacao_config (chave, valor) "
                     "VALUES (?, ?)", (chave, valor))
    conn.execute(
        "INSERT OR IGNORE INTO atualizacao_status (id, cor, mensagem) "
        "VALUES (1, 'VERMELHO', 'ainda nao verificado nesta instalacao')")
    conn.commit()


# ============================================================== estado e config
def config(conn: sqlite3.Connection) -> dict:
    bruto = dict(conn.execute("SELECT chave, valor FROM atualizacao_config"))
    try:
        intervalo = int(bruto.get("intervalo_minutos") or INTERVALO_PADRAO)
    except ValueError:
        intervalo = INTERVALO_PADRAO
    intervalo = max(INTERVALO_MINIMO, min(intervalo, 60 * 24 * 365))
    ultima = bruto.get("ultima_verificacao_em") or ""
    return {
        "intervalo_minutos": intervalo,
        "auto_ligado": bruto.get("auto_ligado", "1") == "1",
        "ultima_verificacao_em": ultima or None,
        "pasta_aplicada": bruto.get("pasta_aplicada") or None,
    }


def _cfg(conn, chave: str) -> str | None:
    r = conn.execute("SELECT valor FROM atualizacao_config WHERE chave = ?",
                     (chave,)).fetchone()
    return r[0] if r else None


def _cfg_set(conn, chave: str, valor: str) -> None:
    conn.execute("INSERT INTO atualizacao_config (chave, valor) VALUES (?, ?) "
                 "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
                 (chave, str(valor)))


def _status(conn, **campos) -> None:
    # `None` é um valor legítimo aqui: passar `etapa=None` é justamente o
    # modo de limpar a etapa quando a execução termina. Filtrar os `None`
    # deixaria a tela presa em "aplicando" para sempre. Só o que não foi
    # dito fica de fora, e isso se resolve com o filtro do chamador.
    campos = {k: v for k, v in campos.items() if v is not _AUSENTE}
    if not campos:
        return
    sets = ", ".join(f"{k} = ?" for k in campos)
    conn.execute(f"UPDATE atualizacao_status SET {sets} WHERE id = 1", tuple(campos.values()))


def _venceu(cfg: dict, agora: datetime | None = None) -> tuple[bool, int | None]:
    """A verificação está vencida? Quanto falta, em minutos.

    Sem `ultima_verificacao_em` vence na hora — nunca houve verificação, então
    o estado honesto é "desatualizado". A diferença é sempre em minutos
    decorridos, não em horário do dia: é isso que faz a contagem sobreviver a
    servidor desligado.
    """
    agora = agora or _agora()
    if not cfg["ultima_verificacao_em"]:
        return True, None
    try:
        ultima = datetime.fromisoformat(cfg["ultima_verificacao_em"])
    except ValueError:
        return True, None
    if ultima.tzinfo is None:
        ultima = ultima.replace(tzinfo=agora.tzinfo)
    faltan = cfg["intervalo_minutos"] - (agora - ultima).total_seconds() / 60
    return faltan <= 0, int(round(faltan))


# ================================================================== a pasta
def listar_pastas() -> dict:
    """Pastas candidatas, da mais recente para a mais antiga."""
    if not PASTA_UPDATE.exists():
        return {"pasta": None, "pastas": [], "ignoradas": []}
    pastas, ignoradas = [], []
    for p in sorted(PASTA_UPDATE.iterdir(), reverse=True):
        if not p.is_dir():
            continue
        if NOME_PASTA.match(p.name):
            pastas.append(p.name)
        else:
            ignoradas.append(p.name)
    return {"pasta": pastas[0] if pastas else None, "pastas": pastas,
            "ignoradas": ignoradas}


def _colunas(conn: sqlite3.Connection, tabela: str) -> dict[str, str]:
    """Mapa `coluna normalizada -> coluna real`."""
    return {_norm(c[1]): c[1] for c in conn.execute(f"PRAGMA table_info({tabela})")}


def _deduzir_tabela(nome: str, cabecalho: list[str]) -> str | None:
    """Qual tabela o arquivo alimenta.

    Primeiro o nome do arquivo, porque ele é a intenção de quem montou a pasta.
    Só quando o nome não diz nada é que o conteúdo decide, e aí a chave mais
    específica vence: `codigo_aeronave` só existe em `registro_sisant`.
    """
    pedacos = [p for p in re.split(r"[^a-z0-9]+", nome.lower()) if p]
    for pedaco in reversed(pedacos):
        if pedaco in TABELA_POR_NOME:
            return TABELA_POR_NOME[pedaco]
    chaves = {_norm(c) for c in cabecalho}
    if "codigoaeronave" in chaves:
        return "registro_sisant"
    if "icao" in chaves and ("ciad" in chaves or "municipio" in chaves):
        return "aerodromo"
    if "ncertmatricula" in chaves or ("dsmodelo" in chaves and "nmfabricante" in chaves):
        return "aeronave"
    if "razaosocial" in chaves and "cnpj" in chaves:
        return "org_producao"
    if "orgnabrev" in chaves or "orgcodigo" in chaves:
        return "fabricante"
    if "dsmodelo" in chaves:
        return "modelo"
    if "documento" in chaves or "natureza" in chaves:
        return "pessoa"
    return "marca" if "nome" in chaves else None


def ler_registros(caminho: Path) -> list[dict]:
    """`.json` (lista de objetos) ou `.csv`/`.txt` (delimitado)."""
    if caminho.suffix.lower() == ".json":
        # O mesmo reparo de escape do ETL: a ANAC exporta `"` invertido, e um
        # `json.loads` direto morreria num snapshot inteiro.
        bruto = caminho.read_text(encoding="utf-8-sig", errors="replace")
        dados = json.loads(repair_json_anac(bruto))
        if isinstance(dados, dict):
            for chave in ("data", "records", "registros", "itens", "rows"):
                if isinstance(dados.get(chave), list):
                    return dados[chave]
            return [dados]
        if not isinstance(dados, list):
            raise ValueError(f"{caminho.name}: esperava lista de registros")
        return [r for r in dados if isinstance(r, dict)]
    cabecalho, corpo = read_delimited(caminho)
    return [dict(zip(cabecalho, linha)) for linha in corpo]


# ============================================================== aplicar diff
def _mesmo(a, b) -> bool:
    """Dois valores são o mesmo para efeito de gravação?

    `'SBGR'` e `' sbgr '` não são o mesmo dado: a coluna tem grafia própria e o
    arquivo novo pode estar errado. Já `'1'` e `'1.0'` num inteiro são o mesmo
    número, e gravar de novo seria ruído.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    sa, sb = str(a).strip(), str(b).strip()
    if sa == sb:
        return True
    try:
        return float(sa.replace(",", ".")) == float(sb.replace(",", "."))
    except ValueError:
        return False


def _cabeçalho(conn, tabela: str) -> tuple[str, dict, list[str]]:
    """(coluna-chave, mapa normalizado->real, colunas aceitas para chave)."""
    if tabela not in CHAVE:
        raise KeyError(tabela)
    coluna, aceitas = CHAVE[tabela]
    return coluna, _colunas(conn, tabela), aceitas


def _localizar(conn, tabela, coluna, mapa, aceitas, registro) -> int | None:
    """Primary key do registro existente, ou `None` se ele ainda não existe."""
    for chave_norm in aceitas:
        real = mapa.get(_norm(chave_norm))
        if not real:
            continue
        valor = registro.get(chave_norm) or registro.get(real)
        if valor in (None, ""):
            continue
        if real == coluna:
            return _texto(valor)
        achado = conn.execute(
            f"SELECT {coluna} FROM {tabela} WHERE {real} = ? LIMIT 1",
            (str(valor).strip(),)).fetchone()
        if achado:
            return achado[0]
    return None


def _completar_para_inserir(conn, tabela: str,
                            colunas: list[str], valores: list) -> tuple[list, list]:
    """Preenche o que o arquivo não trouxe e a tabela exige.

    Só dois casos, e ambos são dedutíveis do próprio arquivo:

    - `pessoa.chave` é NOT NULL e é a identidade estável do cadastro. Quando o
      arquivo traz `id`, a chave é `P{id}`; quando traz documento, é o
      documento normalizado.
    - `pessoa.natureza` é NOT NULL e sai do tamanho do documento: 14 dígitos é
      CNPJ (JURIDICA), 11 é CPF (FISICA).

    O que não for dedutível fica de fora e o `INSERT` ainda assim falha com um
    aviso — melhor um erro honesto do que um cadastro inventado.
    """
    faltando = [d[1] for d in conn.execute(f"PRAGMA table_info({tabela})")
                if d[3] and d[4] is None and d[1] not in colunas]
    if not faltando or tabela != "pessoa":
        return colunas, valores

    def _tem(campo):
        return valores[colunas.index(campo)] if campo in colunas else None

    documento = re.sub(r"\D", "", _tem("documento_bruto") or _tem("documento") or "")
    if "natureza" in faltando:
        natureza = "JURIDICA" if len(documento) == 14 else "FISICA"
        colunas.append("natureza"); valores.append(natureza)
        faltando.remove("natureza")
    if "chave" in faltando:
        if documento:
            chave = documento
        elif _tem("id"):
            chave = "P" + str(_tem("id"))
        else:
            return colunas, valores          # sem identidade: deixa o NOT NULL barrar
        colunas.append("chave"); valores.append(chave)
    return colunas, valores


def aplicar_arquivo(conn, execucao_id: int, caminho: Path,
                    registro_sha: str, relatorio: dict) -> dict:
    """Compara um arquivo com o banco e grava **só o que difere**.

    Devolve o resumo do arquivo. Um arquivo em que nada mudou devolve
    `campos = 0` e não executa um único `UPDATE` — que é o comportamento
    principal do botão.
    """
    nome = caminho.name
    try:
        registros = ler_registros(caminho)
    except Exception as exc:                       # noqa: BLE001
        relatorio["erros"].append(f"{nome}: {exc}")
        return {"arquivo": nome, "registros": 0, "campos": 0, "novos": 0,
                "tabela": None, "erro": str(exc)}

    if not registros:
        relatorio["erros"].append(f"{nome}: nenhum registro")
        return {"arquivo": nome, "registros": 0, "campos": 0, "novos": 0,
                "tabela": None, "erro": "vazio"}

    cabecalho = list(registros[0])
    tabela = _deduzir_tabela(caminho.stem, cabecalho)
    if tabela is None or tabela not in CHAVE:
        motivo = f"tabela não reconhecida (colunas: {', '.join(cabecalho[:6])})"
        relatorio["erros"].append(f"{nome}: {motivo}")
        return {"arquivo": nome, "registros": len(registros), "campos": 0,
                "novos": 0, "tabela": None, "erro": motivo}

    coluna, mapa, aceitas = _cabeçalho(conn, tabela)
    # Primary key nunca entra no UPDATE: mudá-la criaria outro registro.
    mapa_escrita = {k: v for k, v in mapa.items() if v != coluna}
    desc = [d[0] for d in conn.execute(f"SELECT * FROM {tabela} LIMIT 0").description]

    res = {"arquivo": nome, "tabela": tabela, "registros": len(registros),
           "campos": 0, "novos": 0, "ignorados": 0, "desconhecidas": set()}

    for registro in registros:
        # Normaliza as chaves do registro uma vez. Sem isso, cada campo
        # refazia o mesmo `set` de normalização — 40 colunas vezes 40 colunas
        # por registro.
        bruto_norm = {_norm(k): v for k, v in registro.items() if isinstance(k, str)}
        chave = _localizar(conn, tabela, coluna, mapa, aceitas, registro)
        if chave is None:
            valores, colunas = [], []
            for nome_col, real in mapa.items():
                bruto = bruto_norm.get(nome_col)
                if bruto in (None, ""):
                    continue
                colunas.append(real); valores.append(str(bruto).strip())
            if not colunas:
                res["ignorados"] += 1
                continue
            # Um arquivo de atualização quase nunca traz todas as colunas
            # obrigatórias. Sem isto, `INSERT` morre num NOT NULL e o registro
            # novo é perdido com um aviso genérico — quando o que falta é só
            # um campo com valor derivável.
            colunas, valores = _completar_para_inserir(conn, tabela, colunas, valores)
            try:
                conn.execute(f"INSERT INTO {tabela} ({', '.join(colunas)}) "
                             f"VALUES ({', '.join('?' for _ in colunas)})", valores)
                res["novos"] += 1
            except sqlite3.Error as exc:
                relatorio["erros"].append(f"{nome}: inserção: {exc}")
            continue

        atual = conn.execute(
            f"SELECT * FROM {tabela} WHERE {coluna} = ?", (chave,)).fetchone()
        if atual is None:
            res["ignorados"] += 1
            continue
        fonte = dict(zip(desc, atual))
        # Só os campos que o arquivo realmente traz entram na comparação. Sem
        # este teste, um arquivo com uma coluna sozinha zeraria as outras 39.
        mudancas = []
        for nome_col, real in mapa_escrita.items():
            if nome_col not in bruto_norm:
                continue
            novo = _texto(bruto_norm[nome_col])
            if _mesmo(fonte.get(real), novo):
                continue
            mudancas.append((real, fonte.get(real), novo))
        if not mudancas:
            continue
        sets = ", ".join(f"{real} = ?" for real, _, _ in mudancas)
        conn.execute(f"UPDATE {tabela} SET {sets} WHERE {coluna} = ?",
                     (*[n for _, _, n in mudancas], chave))
        rotulo = _texto(fonte.get("nome") or fonte.get("nr_cert_matricula")
                        or fonte.get("razao_social") or chave)
        for real, antes, depois in mudancas:
            conn.execute(
                "INSERT INTO atualizacao_diferenca (execucao_id, tabela, chave, "
                "rotulo, campo, antes, depois, origem) VALUES (?,?,?,?,?,?,?,?)",
                (execucao_id, tabela, str(chave), rotulo, real,
                 None if antes is None else str(antes),
                 None if depois is None else str(depois),
                 f"update/{caminho.parent.name}/{nome}"))
            res["campos"] += 1

    res["desconhecidas"] = sorted(res.pop("desconhecidas"))
    return res


# ================================================================== executar
EXTENSOES = (".json", ".csv", ".txt", ".tsv")


def verificar(conn: sqlite3.Connection, origem: str = "manual") -> dict:
    """Verificação completa: escolhe a pasta, compara e grava só as diferenças.

    Todo o corpo roda sob um lock, porque o botão manual e o agendador podem
    disparar ao mesmo tempo e dois `UPDATE` concorrentes no mesmo SQLite dariam
    `database is locked` no meio da aplicação das diferenças.
    """
    with _lock:
        return _verificar(conn, origem)


def _verificar(conn, origem: str) -> dict:
    inicio = time.monotonic()
    agora = _agora()
    # `concluida_em` precisa ser apagado aqui, não só no fim: `situacao()`
    # deduz "em execução" de `iniciada_em` presente **e** `concluida_em`
    # ausente. Sem esta linha, a rodada anterior deixaria o carimbo velho no
    # registro e a tela ficaria verde durante toda a execução nova.
    _status(conn, cor="AMARELO", etapa="procurando pasta",
            mensagem="procurando a pasta mais recente em update/",
            iniciada_em=_iso(agora), concluida_em=None)
    conn.commit()

    exec_id = conn.execute(
        "INSERT INTO atualizacao_execucao (origem, iniciada_em, estado) "
        "VALUES (?, ?, 'rodando')", (origem, _iso(agora))).lastrowid
    conn.commit()

    try:
        onde = listar_pastas()
        pasta = onde["pasta"]
        relatorio: dict = {"execucao_id": exec_id, "origem": origem,
                           "pasta": pasta, "pastas": onde["pastas"],
                           "ignoradas": onde["ignoradas"], "arquivos": [],
                           "erros": []}

        if pasta is None:
            relatorio["estado"] = "ignorado"
            relatorio["mensagem"] = (
                "nenhuma pasta com data (yyyymmdd) em update/" if not onde["ignoradas"]
                else "pastas ignoradas (nome fora do formato): "
                     + ", ".join(onde["ignoradas"]))
        else:
            _status(conn, etapa="analisando",
                    mensagem=f"comparando {pasta} com os cadastros", pasta=pasta)
            conn.commit()
            destino = PASTA_UPDATE / pasta
            arquivos = sorted(
                (p for p in destino.iterdir()
                 if p.is_file() and p.suffix.lower() in EXTENSOES),
                key=lambda p: p.name)
            for caminho in arquivos:
                _status(conn, etapa="aplicando", pasta=pasta,
                        mensagem=f"lendo {caminho.name}")
                conn.commit()
                relatorio["arquivos"].append(
                    aplicar_arquivo(conn, exec_id, caminho, "", relatorio))
            conn.commit()
            # A pasta conferida passa a ser a referência do "já aplicado",
            # mesmo sem ter mudado nada: ela já foi conferida, e reconferi-la na
            # próxima verificação é trabalho jogado fora.
            _cfg_set(conn, "pasta_aplicada", pasta)
            relatorio["estado"] = "ok"
    except Exception as exc:                       # noqa: BLE001
        relatorio = {"execucao_id": exec_id, "origem": origem, "pasta": None,
                     "arquivos": [], "erros": [str(exc)], "estado": "erro",
                     "mensagem": f"{type(exc).__name__}: {exc}"}

    dur = round(time.monotonic() - inicio, 3)
    registros = sum(a.get("registros", 0) for a in relatorio.get("arquivos", []))
    campos = sum(a.get("campos", 0) for a in relatorio.get("arquivos", []))
    novos = sum(a.get("novos", 0) for a in relatorio.get("arquivos", []))
    relatorio.update({"registros": registros, "campos": campos, "novos": novos,
                      "duracao_s": dur, "arquivos_n": len(relatorio.get("arquivos", []))})
    # A mensagem resume os totais, então só pode ser montada depois deles
    # existirem. Se um arquivo falhou no meio, o texto do erro manda no lugar.
    if relatorio["estado"] != "erro":
        relatorio["mensagem"] = _resumo(relatorio)

    conn.execute(
        "UPDATE atualizacao_execucao SET concluida_em = ?, estado = ?, mensagem = ?, "
        "arquivos = ?, registros = ?, campos = ?, novos = ?, duracao_s = ? "
        "WHERE id = ?",
        (_iso(), relatorio["estado"], relatorio["mensagem"],
         relatorio["arquivos_n"], registros, campos, novos, dur, exec_id))
    _status(conn, cor="VERDE", etapa=None, mensagem=relatorio["mensagem"],
            concluida_em=_iso(), registros=registros, campos=campos, novos=novos,
            duracao_s=dur)
    _cfg_set(conn, "ultima_verificacao_em", _iso())
    conn.commit()
    return relatorio


def _resumo(relatorio: dict) -> str:
    arquivos = relatorio.get("arquivos") or []
    if not arquivos:
        return "pasta vazia: nada a comparar"
    mudou = [a for a in arquivos if a.get("campos") or a.get("novos")]
    if not mudou:
        return (f"{len(arquivos)} arquivo(s) conferido(s): nenhum campo difere "
                f"de {relatorio['registros']} registro(s)")
    partes = []
    if relatorio["campos"]:
        partes.append(f"{relatorio['campos']} campo(s) alterado(s)")
    if relatorio["novos"]:
        partes.append(f"{relatorio['novos']} registro(s) novo(s)")
    erros = relatorio.get("erros") or []
    return (f"{len(arquivos)} arquivo(s), {relatorio['registros']} registro(s): "
            + " e ".join(partes)
            + (f" · {len(erros)} aviso(s)" if erros else ""))


# ================================================================== agendador
def situacao(conn: sqlite3.Connection) -> dict:
    """O que a tela precisa para o ícone e a contagem.

    `faltam_minutos` é negativo quando venceu. A cor é derivada da situação, não
    guardada à parte: 🔴 vencido, 🟡 em execução, 🟢 dentro do intervalo.
    """
    cfg = config(conn)
    linha = conn.execute("SELECT * FROM atualizacao_status WHERE id = 1").fetchone()
    nomes = [d[0] for d in conn.execute(
        "SELECT * FROM atualizacao_status LIMIT 0").description]
    status = dict(zip(nomes, linha)) if linha else {}

    venceu, faltam = _venceu(cfg)
    rodando = bool(status.get("iniciada_em")) and status.get("concluida_em") is None
    if rodando:
        cor = "AMARELO"
    elif venceu:
        cor = "VERMELHO"
    else:
        cor = "VERDE"

    return {
        "cor": cor,
        "rotulo": {"VERMELHO": "desatualizado", "AMARELO": "atualizando",
                   "VERDE": "atualizado"}[cor],
        "etapa": status.get("etapa"),
        "mensagem": status.get("mensagem"),
        "pasta": status.get("pasta"),
        "iniciada_em": status.get("iniciada_em"),
        "concluida_em": status.get("concluida_em"),
        "registros": status.get("registros"),
        "campos": status.get("campos"),
        "novos": status.get("novos"),
        "duracao_s": status.get("duracao_s"),
        "intervalo_minutos": cfg["intervalo_minutos"],
        "auto_ligado": cfg["auto_ligado"],
        "ultima_verificacao_em": cfg["ultima_verificacao_em"],
        "pasta_aplicada": cfg["pasta_aplicada"],
        "faltam_minutos": faltam,
        "venceu": venceu,
        "pastas": listar_pastas(),
    }


def salvar_config(conn: sqlite3.Connection, dados: dict) -> dict:
    """Grava o intervalo e o liga/desliga. Devolve a situação recalculada."""
    if "intervalo_minutos" in dados:
        bruto = _numero(dados["intervalo_minutos"], None)
        if bruto is None:
            raise ValueError("intervalo_minutos deve ser um número de minutos")
        inteiro = int(bruto)
        if inteiro < INTERVALO_MINIMO:
            raise ValueError(f"o intervalo mínimo é {INTERVALO_MINIMO} minutos")
        if inteiro > 60 * 24 * 365:
            raise ValueError("o intervalo máximo é um ano (525600 minutos)")
        _cfg_set(conn, "intervalo_minutos", inteiro)
    if "auto_ligado" in dados:
        _cfg_set(conn, "auto_ligado", "1" if dados["auto_ligado"] else "0")
    conn.commit()
    return situacao(conn)


def _numero(valor, padrao=None):
    try:
        return float(str(valor).strip().replace(".", "").replace(",", "."))
    except (TypeError, ValueError):
        return padrao


def historico(conn: sqlite3.Connection, limite: int = 20) -> list[dict]:
    """Execuções recentes e as diferenças que cada uma gravou."""
    linhas = conn.execute(
        "SELECT id, origem, pasta, iniciada_em, concluida_em, estado, mensagem, "
        "arquivos, registros, campos, novos, duracao_s "
        "FROM atualizacao_execucao ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
    nomes = ("id", "origem", "pasta", "iniciada_em", "concluida_em", "estado",
             "mensagem", "arquivos", "registros", "campos", "novos", "duracao_s")
    saida = [dict(zip(nomes, l)) for l in linhas]
    for item in saida:
        item["diferencas"] = [dict(zip(
            ("tabela", "chave", "rotulo", "campo", "antes", "depois", "origem"), l))
            for l in conn.execute(
                "SELECT tabela, chave, rotulo, campo, antes, depois, origem "
                "FROM atualizacao_diferenca WHERE execucao_id = ? LIMIT 200",
                (item["id"],))]
    return saida


def _loop() -> None:
    """Verificação periódica em thread própria.

    Duas regras:

    - **Dispara no arranque.** A primeira volta acontece já na subida, não depois
      de um intervalo inteiro. Quem liga o servidor quer o estado atual, não o
      estado de ontem.
    - **Dorme em parcelas curtas.** Um `time.sleep(intervalo)` de 24 horas é
      impossível de cancelar no `Ctrl+C`: o servidor ficaria preso. Dormir de 30
      em 30 segundos e reavaliar faz a parada ser quase imediata.

    O que dispara é `_venceu`, que compara `ultima_verificacao_em` com agora.
    Como o carimbo fica gravado no banco, desligar o servidor por três dias e
    ligá-lo de novo faz a verificação vencer na hora — a contagem não depende de
    o processo ter estado vivo.
    """
    print("  atualizacao: verificacao automatica ligada")
    while not _parar.is_set():
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            try:
                aplicar_schema(conn)
                cfg = config(conn)
                venceu, _ = _venceu(cfg)
            finally:
                conn.close()
            if venceu and cfg["auto_ligado"]:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                try:
                    rel = verificar(conn, "automatica")
                    print(f"  atualizacao automatica: {rel['mensagem']}")
                except Exception as exc:          # noqa: BLE001
                    print(f"  atualizacao automatica falhou: {exc}")
                finally:
                    conn.close()
            elif not cfg["auto_ligado"]:
                # Desligado: espera o intervalo curto e olha de novo, para que
                # religar na tela dispare sem reiniciar o servidor.
                _parar.wait(30)
                continue
        except Exception as exc:                  # noqa: BLE001
            print(f"  atualizacao: {exc}")
        _parar.wait(30)


def iniciar(conectar=None) -> None:
    """Liga a thread uma vez só."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _parar.clear()
    _thread = threading.Thread(target=_loop, name="atualizacao", daemon=True)
    _thread.start()


def parar() -> None:
    _parar.set()


def rodando() -> bool:
    return _thread is not None and _thread.is_alive()
