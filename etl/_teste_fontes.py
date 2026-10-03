"""Publica arquivos de exemplo e testa o cadastro de fontes pela rota HTTP.

Os arquivos nascem de verdade aqui — o `.docx` e o `.xlsx` são ZIP com XML
montado à mão — porque o objetivo é provar a conversão, não simular o
resultado dela.

Sobe um `http.server` efêmero na porta 8791, cadastra cada link por
`POST /api/fontes`, mostra o que veio de volta e apaga tudo no final.
"""

import json
import functools
import pathlib
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "etl"))

EXEMPLO = RAIZ / "build" / "_fontes_exemplo"
SERVIDOR = "http://127.0.0.1:8791/"
BANCO = RAIZ / "build" / "anac.db"


# --------------------------------------------------------------- montagem
def fazer_docx(destino: pathlib.Path) -> None:
    """Um .docx real: ZIP com `[Content_Types].xml` e `word/document.xml`."""
    conteudo = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>Relatorio de teste da conversao</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Aerodromo SBGR Guarulhos, situacao cadastrado.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("word/document.xml", conteudo)


def fazer_xlsx(destino: pathlib.Path) -> None:
    """Um .xlsx real: ZIP com sharedStrings e uma planilha."""
    rel = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    tipos = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    compartilhadas = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" '
        'uniqueCount="3">'
        "<si><t>ICAO</t></si><si><t>Nome</t></si><si><t>Situacao</t></si>"
        "</sst>"
    )
    planilha = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
        '<c r="C1" t="s"><v>2</v></c></row>'
        '<row r="2"><c r="A2"><v>SBGR</v></c><c r="B2"><v>Guarulhos</v></c>'
        "<c r=\"C2\"><v>Cadastrado</v></c></row>"
        "</sheetData></worksheet>"
    )
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rel)
        z.writestr("xl/sharedStrings.xml", compartilhadas)
        z.writestr("xl/worksheets/sheet1.xml", planilha)


def fazer_xls_csv(destino: pathlib.Path) -> None:
    """`.xls` que é CSV por dentro — o caso comum que o sistema deve converter."""
    destino.write_text("ICAO;Nome;Situacao\nSBGR;Guarulhos;Cadastrado\n",
                       encoding="utf-8")


def fazer_ole2(destino: pathlib.Path) -> None:
    """`.doc` binário de verdade: assinatura OLE2, que não converte."""
    destino.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)


def fazer_pdf(destino: pathlib.Path) -> None:
    destino.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n")


def fazer_todos() -> None:
    EXEMPLO.mkdir(parents=True, exist_ok=True)
    (EXEMPLO / "aeronaves.csv").write_text(
        "matricula;modelo;situacao\nPT-ABC;EMBRAER 737;ATIVO\n", encoding="utf-8")
    (EXEMPLO / "aeroportos.json").write_text(json.dumps(
        [{"icao": "SBGR", "nome": "Guarulhos", "situacao": "Cadastrado"}],
        ensure_ascii=False), encoding="utf-8")
    (EXEMPLO / "relatorio.txt").write_text(
        "Texto simples para conferir a conversao.\n", encoding="utf-8")
    (EXEMPLO / "metadados.md").write_text(
        "# Metadados\n\nFonte cadastrada a mao.\n", encoding="utf-8")
    fazer_docx(EXEMPLO / "relatorio.docx")
    fazer_xlsx(EXEMPLO / "planilha.xlsx")
    fazer_xls_csv(EXEMPLO / "antigo.xls")
    fazer_ole2(EXEMPLO / "binario.doc")
    fazer_pdf(EXEMPLO / "documento.pdf")


# ------------------------------------------------------------------ helpers
def chamar(metodo: str, caminho: str, corpo: dict | None = None) -> tuple[int, dict]:
    url = "http://127.0.0.1:8730" + caminho
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(url, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8")
        try:
            return e.code, json.loads(corpo)
        except ValueError:
            return e.code, {"bruto": corpo[:300]}


def main() -> None:
    fazer_todos()
    # `directory` é argumento do construtor, não atributo de classe: passar
    # como atributo deixa o servidor servindo o diretório atual e tudo dá 404.
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(EXEMPLO))
    httpd = ThreadingHTTPServer(("127.0.0.1", 8791), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    casos = [
        ("aeronaves.csv", "metadado csv"),
        ("aeroportos.json", "metadado json"),
        ("relatorio.txt", "metadado txt"),
        ("metadados.md", "metadado md"),
        ("relatorio.docx", "documento docx"),
        ("planilha.xlsx", "planilha xlsx"),
        ("antigo.xls", "xls que e csv"),
        ("binario.doc", "doc binario OLE2"),
        ("documento.pdf", "pdf"),
    ]

    criados = []
    print("=== cadastro dos links ===")
    for arquivo, rotulo in casos:
        st, r = chamar("POST", "/api/fontes", {
            "titulo": "Teste " + rotulo,
            "url": SERVIDOR + arquivo,
            "origem": "verificacao automatica"})
        marca = "OK " if st == 200 else "ERRO"
        print("  %s %-18s http=%s  status=%-12s motivo=%s" % (
            marca, arquivo, st, r.get("status"),
            (r.get("erro") or "-")[:52]))
        if st == 200:
            criados.append(r["id"])

    print("\n=== conteudo convertido de cada um ===")
    for fid in criados:
        st, r = chamar("GET", "/api/fontes/%d" % fid)
        doc = (r.get("documento_md") or "")
        linhas = [l for l in doc.splitlines() if l.strip()]
        print("  #%-3s %-22s %-9s %-12s %s" % (
            fid, (r.get("titulo") or "?")[:22],
            r.get("formato_arquivo"), r.get("status"),
            (linhas[0][:44] if linhas else "(sem markdown)")))

    print("\n=== exclusao ===")
    for fid in criados:
        st, _ = chamar("DELETE", "/api/fontes/%d" % fid)
        print("  fonte %-3s excluida http=%s" % (fid, st))
    st, r = chamar("GET", "/api/fontes")
    print("  sobraram:", len(r) if isinstance(r, list) else r)

    httpd.shutdown()


if __name__ == "__main__":
    main()