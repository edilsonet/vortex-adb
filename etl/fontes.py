"""Links de fonte externa e conversão para Markdown.

Cada link cadastrado aponta para um arquivo `json`, `csv`, `doc`, `docx`,
`xls`, `xlsx`, `pdf`, `txt` ou `md`, e vira um documento Markdown legível na
própria tela — sem depender do navegador abrir o formato e sem instalar nada.

**A conversão é feita com a biblioteca padrão, e isso define o que funciona.**

`docx` e `xlsx` são arquivos ZIP com XML dentro: dá para ler `word/document.xml`
e `xl/sharedStrings.xml` + `xl/worksheets/sheet1.xml` com `zipfile` e
`xml.etree`. `csv` e `txt` são texto. Portanto esses quatro convertem de
verdade, e não por aproximação.

O que não converte é o formato binário antigo: `doc` e `xls` são OLE2 (composto
porStreams, assinatura `D0 CF 11 E0`) e `pdf` tem tabela de compressão própria.
Extrair texto dessas três sem biblioteca é reescrever um leitor de Compound
File Binary Format. Nesses casos o arquivo original é **preservado e
catalogado**, e a tela diz o motivo — em vez de fingir que converteu, ou de
engolir o erro.

Um detalhe que aparece na prática: muita gente salva `.xls` quando o conteúdo é
CSV ou HTML. O `xls` desses é convertido de boa; o OLE2 de verdade não.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
import unicodedata
import urllib.error
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

FORMATO = {
    "json": "json", "csv": "csv", "doc": "documento", "docx": "documento",
    "xls": "planilha", "xlsx": "planilha", "pdf": "documento", "txt": "texto",
    "md": "markdown",
}
CONVERTE = {"json", "csv", "docx", "xlsx", "txt", "md"}
ACEITOS = tuple(FORMATO)
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
LIMITE_BYTES = 25 * 1024 * 1024
TIMEOUT = 30
USER_AGENT = "anac-db/1.0 (registro local)"


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _norm(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def aplicar_schema(conn: sqlite3.Connection) -> None:
    from atualizacao import aplicar_schema as _a
    _a(conn)


# ================================================================ conversão
def _md_titulo(texto: str, titulo: str | None) -> str:
    return f"# {titulo}\n\n{texto.strip()}\n" if titulo else texto.strip() + "\n"


def json_para_md(bruto: bytes, titulo: str | None) -> str:
    dados = json.loads(bruto.decode("utf-8-sig", errors="replace"))
    if isinstance(dados, list) and dados and isinstance(dados[0], dict):
        colunas = list(dados[0])
        linhas = [" | ".join(colunas), " | ".join("---" for _ in colunas)]
        for reg in dados:
            linhas.append(" | ".join(
                str(reg.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in colunas))
        return _md_titulo("\n".join(linhas), titulo)
    return _md_titulo("```json\n" + json.dumps(dados, ensure_ascii=False,
                                                indent=2) + "\n```", titulo)


def csv_para_md(bruto: bytes, titulo: str | None) -> str:
    texto = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = bruto.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    texto = texto or bruto.decode("utf-8", errors="replace")
    try:
        dialeto = csv.Sniffer().sniff(texto[:8000], delimiters=";,\t|").delimiter
    except csv.Error:
        dialeto = "\t" if texto.count("\t") > texto.count(";") else (
            ";" if texto.count(";") > texto.count(",") else ",")
    linhas = [l for l in csv.reader(io.StringIO(texto), delimiter=dialeto)
              if any(c.strip() for c in l)]
    if not linhas:
        return _md_titulo("_(arquivo vazio)_", titulo)
    cabecalho, corpo = linhas[0], linhas[1:]
    saida = ["| " + " | ".join(c.strip() for c in cabecalho) + " |",
             "| " + " | ".join("---" for _ in cabecalho) + " |"]
    for linha in corpo:
        celulas = (linha + [""] * len(cabecalho))[:len(cabecalho)]
        saida.append("| " + " | ".join(
            c.strip().replace("|", "\\|").replace("\n", " ") for c in celulas) + " |")
    return _md_titulo("\n".join(saida), titulo)


def txt_para_md(bruto: bytes, titulo: str | None) -> str:
    texto = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = bruto.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    texto = texto or bruto.decode("utf-8", errors="replace")
    # Só vira bloco cercado se o arquivo for código; texto corrido continua
    # texto corrido, porque cercar um parágrafo num cercado de 300 linhas
    # deixa o documento ilegível.
    linhas = texto.splitlines()
    parece_codigo = (linhas and sum(1 for l in linhas if l[:1] in (" ", "\t")) > len(linhas) * 0.3)
    corpo = f"```\n{texto.rstrip()}\n```" if parece_codigo else texto.strip()
    return _md_titulo(corpo, titulo)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def docx_para_md(bruto: bytes, titulo: str | None) -> str:
    """`word/document.xml` → Markdown.

    Só `w:p` (parágrafo) e `w:tbl` (tabela) são tratados, e dentro deles `w:t`
    (texto), `w:br` e `w:tab`. Um `.docx` de relatório técnico é isso; o resto da
    especificação OOXML não aparece num documento gerado por gente, e tratar o
    resto seria afirmar uma cobertura que não existe.
    """
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        if "word/document.xml" not in z.namelist():
            raise ValueError("docx sem word/document.xml")
        raiz = ET.fromstring(z.read("word/document.xml"))

    def texto_de(el) -> str:
        partes = []
        for no in el.iter():
            if no.tag == W + "t" and no.text:
                partes.append(no.text)
            elif no.tag == W + "tab":
                partes.append("\t")
            elif no.tag in (W + "br", W + "cr"):
                partes.append("\n")
        return "".join(partes)

    blocos = []
    corpo = raiz.find(W + "body")
    for filho in (corpo if corpo is not None else []):
        if filho.tag == W + "p":
            t = texto_de(filho).strip()
            estilo = filho.find(f"{W}pPr/{W}pStyle")
            nome = (estilo.get(W + "val") or "") if estilo is not None else ""
            if not t:
                blocos.append("")
            elif nome.lower().startswith(("heading", "titulo", "title")):
                n = re.sub(r"\D", "", nome) or "1"
                blocos.append("#" * min(6, int(n) + 1) + " " + t)
            elif nome.lower().startswith(("list", "item")):
                blocos.append("- " + t)
            else:
                blocos.append(t)
        elif filho.tag == W + "tbl":
            linhas = []
            for tr in filho.findall(W + "tr"):
                celulas = [texto_de(tc).strip().replace("|", "\\|")
                           for tc in tr.findall(W + "tc")]
                linhas.append("| " + " | ".join(celulas) + " |")
            if linhas:
                ncol = linhas[0].count("|") - 1
                blocos.append("\n".join(
                    linhas[:1] + ["| " + " | ".join("---" for _ in range(ncol)) + " |"]
                    + linhas[1:]))
    texto = "\n\n".join(b for b in blocos).strip()
    return _md_titulo(texto, titulo)


S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _coluna(indice: int) -> str:
    """1 -> A, 27 -> AA. Sem isso a planilha sai com todas as células na A."""
    nome = ""
    while indice > 0:
        indice, resto = divmod(indice - 1, 26)
        nome = chr(65 + resto) + nome
    return nome


def xlsx_para_md(bruto: bytes, titulo: str | None) -> str:
    """Planilha → tabela Markdown por aba.

    Lê `sharedStrings.xml` para resolver as strings e `sheetN.xml` para as
    células. `zipfile` valida o CRC de cada entrada ao ler, então um arquivo
    corrompido falha aqui em vez de virar lixo silencioso na tela.
    """
    with zipfile.ZipFile(io.BytesIO(bruto)) as z:
        nomes = set(z.namelist())
        compartilhadas: list[str] = []
        if "xl/sharedStrings.xml" in nomes:
            raiz = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in raiz.findall(S + "si"):
                compartilhadas.append("".join(
                    t.text or "" for t in si.iter(S + "t")))
        abas = sorted(n for n in nomes
                      if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        if not abas:
            raise ValueError("xlsx sem worksheets")

        titulos = []
        if "xl/workbook.xml" in nomes:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            for folha in wb.iter(S + "sheet"):
                titulos.append(folha.get("name") or "")

        blocos = []
        for i, aba in enumerate(abas):
            linhas = []
            grade: dict[int, dict[int, str]] = {}
            for linha in ET.fromstring(z.read(aba)).iter(S + "row"):
                indice_linha = int(linha.get("r") or 0)
                for celula in linha.findall(S + "c"):
                    ref = celula.get("r") or ""
                    letras = "".join(ch for ch in ref if ch.isalpha())
                    if not letras:
                        continue
                    col = 0
                    for ch in letras:
                        col = col * 26 + (ord(ch.upper()) - 64)
                    tipo = celula.get("t")
                    if tipo == "inlineStr":
                        valor = "".join(t.text or "" for t in celula.iter(S + "t"))
                    else:
                        v = celula.find(S + "v")
                        bruto_v = v.text if v is not None else None
                        valor = ("" if bruto_v is None else
                                 (compartilhadas[int(bruto_v)]
                                  if tipo == "s" and bruto_v.isdigit()
                                  and int(bruto_v) < len(compartilhadas) else bruto_v))
                    if valor not in (None, ""):
                        grade.setdefault(col, {})[indice_linha] = (
                            str(valor).replace("|", "\\|").replace("\n", " "))
            if grade:
                num_linhas = max(max(v) for v in grade.values())
                colunas = sorted(grade)
                for r in range(1, num_linhas + 1):
                    celulas = [grade.get(c, {}).get(r, "") for c in colunas]
                    if any(celulas):
                        linhas.append("| " + " | ".join(celulas) + " |")
            titulo_aba = titulos[i] if i < len(titulos) else f"aba {i + 1}"
            cabecalho = "| " + " | ".join(_coluna(c) for c in colunas) + " |"
            separador = "| " + " | ".join("---" for _ in colunas) + " |"
            corpo = "\n".join([cabecalho, separador] + linhas) if linhas else cabecalho
            blocos.append(f"## {titulo_aba}\n\n{corpo}")
    return _md_titulo("\n\n".join(blocos), titulo)


# ================================================================== download
def _baixar(url: str) -> bytes:
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("o link precisa começar com http:// ou https://")
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        dados = resp.read(LIMITE_BYTES + 1)
    if len(dados) > LIMITE_BYTES:
        raise ValueError(f"arquivo acima do limite de {LIMITE_BYTES // 1024 // 1024} MB")
    if not dados:
        raise ValueError("resposta vazia")
    return dados


def _formato(url: str) -> str:
    """Formato declarado pelo link; a extensão da URL é a fonte."""
    caminho = url.split("?")[0].split("#")[0].rstrip("/")
    ext = caminho.rsplit(".", 1)[-1].lower() if "." in caminho else ""
    return ext if ext in FORMATO else "txt"


def converter(bruto: bytes, formato: str, titulo: str | None) -> tuple[str | None, str | None]:
    """(markdown, motivo_da_falha).

    Devolver os dois em vez de lançar é o que permite à tela catalogar um `doc`
    que não converte **e** mostrar por quê — em vez de perder o link.
    """
    try:
        if formato == "json":
            return json_para_md(bruto, titulo), None
        if formato == "csv":
            return csv_para_md(bruto, titulo), None
        if formato == "txt":
            return txt_para_md(bruto, titulo), None
        if formato == "md":
            return bruto.decode("utf-8-sig", errors="replace"), None
        if formato == "docx":
            return docx_para_md(bruto, titulo), None
        if formato == "xlsx":
            return xlsx_para_md(bruto, titulo), None
        if formato in ("doc", "xls") and bruto[:8] == OLE2:
            return None, (f"{formato} binário (OLE2): o texto está em streams "
                          "compostos e precisa de biblioteca externa para ser lido. "
                          "O arquivo foi preservado; salve como .docx/.xlsx ou "
                          ".csv para converter.")
        if formato in ("doc", "xls"):
            # Extensão antiga com conteúdo de texto: muito comum quando alguém
            # salva CSV como .xls. Compensa convertendo.
            return txt_para_md(bruto, titulo), None
        if formato == "pdf":
            return None, ("pdf não é convertido: a compressão das páginas é "
                          "proprietária do formato e exigiria biblioteca externa. "
                          "O arquivo foi preservado.")
        return None, f"formato .{formato} sem conversor"
    except Exception as exc:                       # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def coletar(conn: sqlite3.Connection, fid: int, forcar: bool = False) -> dict:
    """Baixa um link, converte e grava. `forcar` ignora o sha256 anterior."""
    linha = conn.execute("SELECT * FROM fonte_link WHERE id = ?", (fid,)).fetchone()
    if linha is None:
        raise LookupError(f"fonte {fid} não existe")
    nomes = [d[0] for d in conn.execute("SELECT * FROM fonte_link LIMIT 0").description]
    fonte = dict(zip(nomes, linha))
    url, titulo = fonte["url"], fonte["titulo"] or fonte["url"].rsplit("/", 1)[-1]

    conn.execute("UPDATE fonte_link SET status = 'baixando', erro = NULL WHERE id = ?",
                 (fid,))
    conn.commit()
    try:
        bruto = _baixar(url)
    except Exception as exc:                       # noqa: BLE001
        motivo = f"{type(exc).__name__}: {exc}"
        conn.execute(
            "UPDATE fonte_link SET status = 'erro', erro = ?, tentativas = "
            "tentativas + 1 WHERE id = ?", (motivo, fid))
        conn.commit()
        return {"id": fid, "status": "erro", "erro": motivo}

    sha = hashlib.sha256(bruto).hexdigest()
    if not forcar and fonte["sha256"] == sha and fonte["documento_md"]:
        conn.execute("UPDATE fonte_link SET status = 'inalterado' WHERE id = ?", (fid,))
        conn.commit()
        return {"id": fid, "status": "inalterado",
                "detalhe": "conteúdo idêntico ao da última coleta"}

    formato = _formato(url)
    md, motivo = converter(bruto, formato, titulo)
    status = "convertido" if md else "sem_conversao"
    conn.execute(
        "UPDATE fonte_link SET documento_md = ?, documento_bruto = NULL, "
        "formato_arquivo = ?, sha256 = ?, tamanho_bytes = ?, status = ?, erro = ?, "
        "tentativas = tentativas + 1, coletado_em = ? WHERE id = ?",
        (md, formato, sha, len(bruto), status, motivo, _agora(), fid))
    conn.commit()
    return {"id": fid, "status": status, "sha256": sha[:12],
            "bytes": len(bruto), "formato": formato, "erro": motivo}


def cadastrar(conn: sqlite3.Connection, dados: dict, coletar_agora: bool = True) -> dict:
    url = (dados.get("url") or "").strip()
    if not url:
        raise ValueError("url é obrigatória")
    formato = (dados.get("formato") or "").strip().lower().lstrip(".")
    if formato and formato not in FORMATO:
        raise ValueError(f"formato inválido: {', '.join(ACEITOS)}")
    formato = formato or _formato(url)
    titulo = (dados.get("titulo") or "").strip() or url.rsplit("/", 1)[-1]

    try:
        fid = conn.execute(
            "INSERT INTO fonte_link (url, titulo, formato_declarado, categoria, "
            "descricao, criado_em) VALUES (?,?,?,?,?,?)",
            (url, titulo, formato, FORMATO[formato],
             (dados.get("descricao") or "").strip() or None, _agora())).lastrowid
        conn.commit()
    except sqlite3.IntegrityError:
        raise ValueError("este link já está cadastrado") from None

    if not coletar_agora:
        return {"id": fid, "status": "pendente"}
    resultado = coletar(conn, fid, forcar=True)
    resultado["id"] = fid
    return resultado


def remover(conn: sqlite3.Connection, fid: int) -> dict:
    conn.execute("DELETE FROM fonte_link WHERE id = ?", (fid,))
    conn.commit()
    return {"ok": True, "id": fid}


def listar(conn: sqlite3.Connection, com_documento: bool = False) -> list[dict]:
    """Os links e, opcionalmente, o Markdown de cada um."""
    colunas = ("id, url, titulo, formato_declarado, formato_arquivo, categoria, "
               "descricao, tamanho_bytes, sha256, status, erro, tentativas, "
               "criado_em, coletado_em")
    extra = ", documento_md" if com_documento else ""
    linhas = conn.execute(f"SELECT {colunas}{extra} FROM fonte_link "
                          "ORDER BY id DESC").fetchall()
    nomes = [c.strip() for c in colunas.split(",")] + (
        ["documento_md"] if com_documento else [])
    return [dict(zip(nomes, l)) for l in linhas]
