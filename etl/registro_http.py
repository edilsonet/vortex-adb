"""Camada HTTP do registro editável.

Separada de `app_server.py` (que tem as consultas) para que cada arquivo tenha
uma responsabilidade só: este entende requisição, status e corpo; aquele
entende banco.

Só biblioteca padrão, escutando em `127.0.0.1`.

**A autenticação vive aqui, no portão.** `acesso.py` sabe quem é o usuário, mas
quem decide "esta rota exige sessão" é este arquivo: uma rota nova nasce
protegida porque `do_GET`/`do_PUT`/... passam por `_exige`, e só as rotas
declaradas em `LIVRES` escapam. O painel estático e a tela de login estão em
`LIVRES` justamente por não terem cookie ainda.

**O "editado por" vem da sessão, não do navegador.** Antes, o nome do operador
chegava no cabeçalho `X-Operador` e qualquer página podia mandar o que
quisesse — o log de auditoria registrava uma mentira escrita pelo cliente. Agora
o nome vem da linha de `usuario` que o cookie aponta.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import acesso as ACESSO
import atualizacao as ATU
import consulta as CST
import fontes as FON
import hidratacao as HID
import registro_db as RD
from app_server import HOST, PORTA_PADRAO, Consulta, ValorErro
from common import BUILD, DB_PATH

ROTA_REGISTRO = re.compile(r"/api/(pessoa|aeronave|aerodromo)/([A-Za-z0-9-]+)")
ROTA_SOCIO = re.compile(r"/api/pessoa/(\d+)/socios")
ROTA_SOCIO_ID = re.compile(r"/api/socios/(\d+)")
# `marca` e `drone` sao chaveados por texto (o prefixo de matricula e o codigo
# SISANT), que pode conter ponto e barra de data. O grupo aceita qualquer coisa
# menos `/`; os demais sao numericos ou OACI, e a classe restrita ja barra o
# resto antes de qualquer consulta ao banco.
CATALOGO = re.compile(r"/api/catalogo/([a-z_]+)")
CATALOGO_ITEM = re.compile(r"/api/catalogo/([a-z_]+)/([^/]+)")
FONTE_ID = re.compile(r"/api/fontes/(\d+)")
USUARIO_ID = re.compile(r"/api/usuarios/(\d+)")
RECUPERACAO = re.compile(r"/api/sessao/recuperar")
SESSAO = re.compile(r"/api/sessao")
SENHA = re.compile(r"/api/senha")
REDEFINIR = re.compile(r"/api/sessao/redefinir")
HIDRO = re.compile(r"/api/hidratacao/([^/]+)")
# `empresa` e `usuario` são linhas de `pessoa`: as duas rotas de catálogo caem
# na mesma tabela, e é a tabela que decide o `int()` do id.
_GRUPO = re.compile(r"(pessoa|aeronave|aerodromo)/([^/]+)")
TABELA_DO_CATALOGO = {
    "empresa": "pessoa", "usuario": "pessoa",
    "aeronave": "aeronave", "aerodromo": "aerodromo",
}


def _fonte(conn, fid: int) -> dict:
    """Um link com o documento convertido; 404 se não existe."""
    for linha in FON.listar(conn, com_documento=True):
        if linha["id"] == fid:
            return linha
    raise ValorErro(f"fonte {fid} não existe")


class NaoAutorizado(Exception):
    """Falha de permissão com o status HTTP que a tela espera."""

    def __init__(self, mensagem: str, status: int = 401):
        super().__init__(mensagem)
        self.status = status


class Handler(BaseHTTPRequestHandler):
    server_version = "RegistroANAC/1.0"
    protocolo = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---------------------------------------------------------------- sessão
    def _cookie(self) -> str | None:
        """O token do cookie `anac_sessao`, ou `None` se não houver.

        Lê a cookie crua e casa o nome com regex: `BaseHTTPRequestHandler` não
        faz parse de cookie e um `split("=")` ingênuo quebraria em valores que
        contenham `=`, como base64.
        """
        for parte in (self.headers.get("Cookie") or "").split(";"):
            achado = re.match(r"\s*" + re.escape(ACESSO.COOKIE) + r"=(\S*)", parte)
            if achado:
                return achado.group(1) or None
        return None

    def _sessao(self, conn=None) -> dict | None:
        """Resolve a sessão numa conexão própria.

        A validação precisa escrever — `sessao_de` renova a validade e apaga as
        expiradas — e por isso não pode ser feita dentro da conexão de outra
        requisição. Num servidor com thread por requisição, uma `sqlite3.connect`
        compartilhada entre threads dá erro de cursor; abrir por chamada
        resolve.
        """
        token = self._cookie()
        if not token:
            return None
        propria = conn is None
        c = conn or ACESSO.abrir(timeout=20)
        try:
            return ACESSO.sessao_de(c, token)
        except sqlite3.Error:
            return None
        finally:
            if propria:
                c.close()

    def _exige(self, conn=None, *, escrita: bool = False,
               admin: bool = False) -> dict:
        """Devolve o usuário ou levanta `NaoAutorizado`.

        `escrita=True` exige papel que grave; `admin=True` exige administrador.
        Erro de acesso vira resposta HTTP com o status certo, para que a tela
        possa diferenciar "entre" de "não pode" de "entre como administrador".
        """
        usuario = self._sessao(conn)
        if usuario is None:
            raise NaoAutorizado("faça login para continuar", 401)
        if admin and not ACESSO.e_admin(usuario):
            raise NaoAutorizado("esta ação é de administrador", 403)
        if escrita and not ACESSO.pode_editar(usuario):
            raise NaoAutorizado(
                f"seu acesso é de consulta: não pode alterar cadastros", 403)
        return usuario

    def _operador(self, usuario: dict) -> str:
        """O nome que vai para a auditoria. Vem da sessão, sempre."""
        return (usuario.get("nome_completo") or usuario.get("email") or "")[:60]

    def _ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    # ------------------------------------------------------------- respostas
    def _cookie_header(self, token: str, *, limpar: bool = False) -> None:
        """`Set-Cookie` da sessão.

        `HttpOnly` porque o token não pode ser lido por JavaScript — é o que
        fecha o roubo por XSS. `SameSite=Lax` porque o app não tem nenhum
        fluxo de terceiros que precise do cookie em POST cruzado.
        """
        if limpar:
            valor = f"{ACESSO.COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
        else:
            valor = (f"{ACESSO.COOKIE}={token}; Path=/; Max-Age="
                     f"{ACESSO.SESSAO_HORAS * 3600}; HttpOnly; SameSite=Lax")
        self.send_header("Set-Cookie", valor)

    def _json(self, dados, status=200, *, cookie: str | None = None,
              limpar_cookie: bool = False):
        corpo = json.dumps(dados, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        if cookie or limpar_cookie:
            self._cookie_header(cookie or "", limpar=limpar_cookie)
        self.end_headers()
        self.wfile.write(corpo)

    def _html(self):
        from registro_ui import HTML
        self._responde(HTML.encode("utf-8"), "text/html; charset=utf-8")

    def _painel(self):
        """O painel estatico, servido do proprio arquivo que `build_dashboard.py`
        gera. O app nao o reescreve: ele so o entrega, e o conteudo continua
        sendo o mesmo arquivo que se abria com duplo clique."""
        caminho = BUILD / "dashboard.html"
        if not caminho.exists():
            return self._json({"erro": "build/dashboard.html ausente: rode "
                                      "etl/build_dashboard.py"}, 404)
        self._responde(caminho.read_bytes(), "text/html; charset=utf-8")

    def _responde(self, corpo: bytes, tipo: str, cache: str = "no-store"):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(corpo)

    def _corpo(self) -> dict:
        bruto = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return json.loads(bruto.decode("utf-8")) if bruto else {}

    def _rota(self):
        u = urlparse(self.path)
        return unquote(u.path), parse_qs(u.query)

    def _com(self, acao):
        """Abre a conexão, roda a ação, fecha — sempre.

        Sem o `finally`, uma exceção no meio deixaria a conexão aberta e o
        `ThreadingHTTPServer` acumularia handles até o SQLite recusar escrita.

        Erro de permissão e erro de acesso viram status HTTP próprio em vez de
        500: a tela precisa dizer "entre" e não "deu ruim".
        """
        conn = ACESSO.abrir(timeout=20)
        try:
            resultado = acao(Consulta(conn))
            if isinstance(resultado, tuple):
                return self._json(resultado[0], resultado[1])
            return self._json(resultado)
        except NaoAutorizado as exc:
            return self._json({"erro": str(exc), "login": exc.status == 401}, exc.status)
        except ACESSO.ErroAcesso as exc:
            return self._json({"erro": str(exc), "login": True}, 401)
        except ValorErro as exc:
            return self._json({"erro": str(exc)}, 400)
        except Exception as exc:      # noqa: BLE001 - a resposta precisa dizer o que houve
            traceback.print_exc()
            return self._json({"erro": f"{type(exc).__name__}: {exc}"}, 500)
        finally:
            conn.close()

    def _ler(self, api, acao):
        """Chama `acao(conn)` depois de exigir sessão.

        Existe para que a exigência de login não fique espalhada por cada rota
        de leitura: uma rota nova que esquecer a guarda só funciona sem cookie
        se o developer lembrar de chamar isto — o que é o comportamento oposto
        do desejado, mas é visível numa linha só, em vez de espalhado.
        """
        self._exige(api.conn)
        return acao(api.conn)

    def _catalogo_item(self, nome: str, chave: str):
        """Detalhe de um registro.

        O `aerodromo` ganha a hidratação junto, porque é o único cadastro cujas
        fontes externas (REDEMET e AISWEB) são consultadas por aeródromo. Sem
        isso, a tela buscaria duas vezes o mesmo dado.
        """
        def pega(api):
            self._exige(api.conn)
            reg = api.detalhe_catalogo(nome, chave)
            if reg is None:
                return {"erro": "registro não encontrado"}, 404
            if nome == "aerodromo":
                reg["hidratacao"] = HID.listar(api.conn, chave)
            return reg
        return self._com(pega)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        caminho, q = self._rota()
        if caminho in ("/", "/app", "/index.html"):
            return self._html()
        if caminho in ("/painel", "/painel.html", "/dashboard.html"):
            return self._painel()
        # Quem sou eu: é a única rota de leitura que responde sem cookie, para
        # que a tela decida entre o app e o formulário de login.
        if SESSAO.fullmatch(caminho):
            return self._sessao_atual()
        if USUARIO_ID.fullmatch(caminho):
            return self._com(lambda api: self._ver_usuario(
                api.conn, int(USUARIO_ID.fullmatch(caminho).group(1))))
        if caminho == "/api/usuarios":
            return self._com(lambda api: self._listar_usuarios(api.conn))
        if caminho == "/api/estado":
            return self._com(lambda api: self._ler(api, api.estado))
        if caminho == "/api/catalogo/contagens":
            return self._com(lambda api: self._ler(api, CST.contagens))
        if caminho == "/api/atualizacao/estado":
            return self._com(lambda api: self._ler(api, ATU.situacao))
        if caminho == "/api/atualizacao/historico":
            return self._com(lambda api: self._ler(api, ATU.historico))
        if caminho == "/api/atualizacao/pastas":
            return self._com(lambda api: self._ler(api, lambda c: ATU.listar_pastas()))
        if caminho == "/api/fontes":
            return self._com(lambda api: self._ler(api, FON.listar))
        m = FONTE_ID.fullmatch(caminho)
        if m:
            fid = int(m.group(1))
            return self._com(lambda api: self._ler(api, lambda c: _fonte(c, fid)))
        m = HIDRO.fullmatch(caminho)
        if m:
            icao = unquote(m.group(1))
            return self._com(lambda api: self._ler(api, lambda c: HID.listar(c, icao)))
        m = CATALOGO_ITEM.fullmatch(caminho)
        if m:
            return self._catalogo_item(m.group(1), unquote(m.group(2)))
        m = CATALOGO.fullmatch(caminho)
        if m:
            cat = m.group(1)
            return self._com(lambda api: self._ler(api, lambda c: api.listar_catalogo(cat, q)))
        if caminho == "/api/pessoas":
            return self._com(lambda api: self._ler(api, lambda c: api.listar_pessoas(q)))
        if caminho == "/api/aeronaves":
            return self._com(lambda api: self._ler(api, lambda c: api.listar("aeronave", q)))
        if caminho == "/api/aerodromos":
            return self._com(lambda api: self._ler(api, lambda c: api.listar("aerodromo", q)))
        m = ROTA_REGISTRO.fullmatch(caminho)
        if m:
            def acha(api):
                self._exige(api.conn)
                reg = api.obter(m.group(1), m.group(2))
                return (reg, 200) if reg else ({"erro": "registro nao encontrado"}, 404)
            return self._com(acha)
        return self._json({"erro": "rota inexistente"}, 404)

    # ----------------------------------------------------------------- PUT
    def do_PUT(self):
        caminho, _ = self._rota()
        m_usuario = USUARIO_ID.fullmatch(caminho)
        if m_usuario:
            uid = int(m_usuario.group(1))
            return self._com(lambda api: self._alterar_usuario(api.conn, uid))
        if SENHA.fullmatch(caminho):
            return self._com(lambda api: self._trocar_propria_senha(api.conn))
        if caminho == "/api/atualizacao/config":
            dados = self._corpo()

            def salva(api):
                try:
                    return ATU.salvar_config(api.conn, dados)
                except ValueError as exc:
                    raise ValorErro(str(exc)) from None
            return self._com(salva)
        # A tela edita por `/api/catalogo/<entidade>/<chave>`; a rota antiga
        # `/api/<entidade>/<chave>` continua valendo. As duas chegam no mesmo
        # lugar porque a trava de campo oficial precisa valer igual nas duas.
        m = ROTA_REGISTRO.fullmatch(caminho)
        if not m:
            mc = CATALOGO_ITEM.fullmatch(caminho)
            if mc:
                m = _GRUPO.match(TABELA_DO_CATALOGO.get(mc.group(1), "") + "/" + mc.group(2))
        if not m:
            return self._json({"erro": "rota inexistente"}, 404)
        # A chave vai como texto: `pessoa` e `aeronave` são id numérico, mas
        # `aerodromo` é chaveado pelo código ICAO, e `int()` aqui transformaria
        # "SBRB" em erro de rota.
        tabela, chave = m.group(1), m.group(2)
        if tabela == "pessoa" and not chave.isdigit():
            return self._json({"erro": "rota inexistente"}, 404)

        def grava(api):
            # A escrita exige sessão e papel que grave; o nome do operador sai
            # da sessão, nunca do corpo da requisição.
            usuario = self._exige(api.conn, escrita=True)
            por = self._operador(usuario)
            dados = self._corpo()
            if tabela == "pessoa":
                return api.salvar_pessoa(int(chave), dados, por)
            return api.salvar_override(tabela, chave, dados, por)
        return self._com(grava)

    # ---------------------------------------------------------------- POST
    def do_POST(self):
        caminho, _ = self._rota()
        # Login, recuperação e redefinição são as três rotas públicas do
        # sistema — nenhuma pode exigir cookie, que é justamente o que o
        # visitante ainda não tem.
        if SESSAO.fullmatch(caminho):
            return self._entrar()
        if RECUPERACAO.fullmatch(caminho):
            return self._recuperar()
        if REDEFINIR.fullmatch(caminho):
            return self._redefinir()
        if caminho == "/api/usuarios":
            dados = self._corpo()
            return self._com(lambda api: self._criar_usuario(api.conn, dados))
        if caminho == "/api/atualizacao/executar":
            return self._com(lambda api: (self._exige(api.conn, escrita=True),
                                          ATU.verificar(api.conn, "manual"))[1])
        if caminho == "/api/fontes":
            dados = self._corpo()

            def cadastra(api):
                self._exige(api.conn, escrita=True)
                try:
                    return FON.cadastrar(api.conn, dados)
                except ValueError as exc:
                    raise ValorErro(str(exc)) from None
            return self._com(cadastra)
        m = HIDRO.fullmatch(caminho)
        if m:
            icao = unquote(m.group(1))

            def hidrata(api):
                self._exige(api.conn, escrita=True)
                try:
                    return HID.hidratar(api.conn, icao)
                except ValueError as exc:
                    raise ValorErro(str(exc)) from None
            return self._com(hidrata)
        m = ROTA_SOCIO.fullmatch(caminho)
        if m:
            chave = int(m.group(1))

            def socio(api):
                usuario = self._exige(api.conn, escrita=True)
                return api.adicionar_socio(chave, self._corpo(),
                                           self._operador(usuario))
            return self._com(socio)
        return self._json({"erro": "rota inexistente"}, 404)

    # -------------------------------------------------------------- DELETE
    def do_DELETE(self):
        caminho, _ = self._rota()
        if SESSAO.fullmatch(caminho):
            return self._sair()
        m = USUARIO_ID.fullmatch(caminho)
        if m:
            uid = int(m.group(1))
            return self._com(lambda api: self._excluir_usuario(api.conn, uid))
        m = FONTE_ID.fullmatch(caminho)
        if m:
            fid = int(m.group(1))
            return self._com(lambda api: (self._exige(api.conn, escrita=True),
                                          FON.remover(api.conn, fid))[1])
        m = ROTA_SOCIO_ID.fullmatch(caminho)
        if m:
            sid = int(m.group(1))
            return self._com(lambda api: (self._exige(api.conn, escrita=True),
                                          api.remover_socio(sid))[1])
        return self._json({"erro": "rota inexistente"}, 404)

    # ------------------------------------------------------- rotas de sessão
    def _sessao_atual(self):
        """`GET /api/sessao` — quem está logado, ou `null` sem cookie.

        Devolve 200 mesmo sem sessão: a tela chama isso para decidir se mostra
        o app ou o formulário, e um 401 aqui seria um sinal errado.
        """
        usuario = self._sessao()
        return self._json(usuario or None)

    def _entrar(self):
        """`POST /api/sessao` com `email` e `senha`."""
        dados = self._corpo()
        conn = ACESSO.abrir(timeout=20)
        try:
            token, usuario = ACESSO.entrar(
                conn, dados.get("email", ""), dados.get("senha", ""),
                ip=self._ip(), agente=self.headers.get("User-Agent", ""))
        except ACESSO.ErroAcesso as exc:
            return self._json({"erro": str(exc)}, 401)
        finally:
            conn.close()
        return self._json({"usuario": usuario}, 200, cookie=token)

    def _sair(self):
        conn = ACESSO.abrir(timeout=20)
        try:
            ACESSO.fechar_sessao(conn, self._cookie())
        finally:
            conn.close()
        return self._json({"ok": True}, 200, limpar_cookie=True)

    def _recuperar(self):
        """`POST /api/sessao/recuperar` — gera o link de nova senha.

        A base vem do `Host` da requisição porque o sistema é local: montar a
        URL com `localhost` fixo quebraria se alguém abrisse por `127.0.0.1`.
        """
        dados = self._corpo()
        conn = ACESSO.abrir(timeout=20)
        try:
            base = f"http://{self.headers.get('Host') or '127.0.0.1:8730'}"
            resposta = ACESSO.pedir_recuperacao(
                conn, dados.get("email", ""), base=base, ip=self._ip(),
                agente=self.headers.get("User-Agent", ""))
        except ACESSO.ErroAcesso as exc:
            return self._json({"erro": str(exc)}, 400)
        finally:
            conn.close()
        return self._json(resposta, 200)

    def _redefinir(self):
        """`POST /api/sessao/redefinir` — consome o token e grava a senha nova."""
        dados = self._corpo()
        conn = ACESSO.abrir(timeout=20)
        try:
            resultado = ACESSO.usar_token(conn, dados.get("token", ""),
                                          dados.get("senha", ""))
        except ACESSO.ErroAcesso as exc:
            return self._json({"erro": str(exc)}, 400)
        finally:
            conn.close()
        return self._json(resultado, 200, limpar_cookie=True)

    def _trocar_propria_senha(self, conn):
        """`PUT /api/senha` — trocar a senha de quem está logado."""
        usuario = self._exige(conn)
        dados = self._corpo()
        try:
            return ACESSO.trocar_senha(conn, usuario["usuario_id"],
                                       dados.get("atual", ""),
                                       dados.get("nova", ""))
        except ACESSO.ErroAcesso as exc:
            raise ValorErro(str(exc)) from None

    # -------------------------------------------------------------- usuários
    def _listar_usuarios(self, conn):
        self._exige(conn, admin=True)
        return ACESSO.listar_usuarios(conn)

    def _ver_usuario(self, conn, uid: int):
        self._exige(conn, admin=True)
        for linha in ACESSO.listar_usuarios(conn):
            if linha["id"] == uid:
                return linha
        return {"erro": "usuário não existe"}, 404

    def _criar_usuario(self, conn, dados: dict):
        self._exige(conn, admin=True)
        try:
            return ACESSO.criar_usuario(conn, dados)
        except ACESSO.ErroAcesso as exc:
            raise ValorErro(str(exc)) from None

    def _alterar_usuario(self, conn, uid: int):
        self._exige(conn, admin=True)
        try:
            return ACESSO.alterar_usuario(conn, uid, self._corpo())
        except ACESSO.ErroAcesso as exc:
            raise ValorErro(str(exc)) from None

    def _excluir_usuario(self, conn, uid: int):
        self._exige(conn, admin=True)
        try:
            return ACESSO.excluir_usuario(conn, uid)
        except ACESSO.ErroAcesso as exc:
            return {"erro": str(exc)}, 400


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Servidor local do registro ANAC")
    ap.add_argument("--porta", type=int, default=PORTA_PADRAO)
    ap.add_argument("--sem-backfill", action="store_true",
                    help="nao roda o backfill de contato no arranque")
    ap.add_argument("--sem-atualizacao", action="store_true",
                    help="nao liga a verificacao automatica da pasta update/")
    args = ap.parse_args(argv)

    if not DB_PATH.exists():
        print(f"banco ausente: {DB_PATH} (rode etl/run.py antes)")
        return 2

    conn = ACESSO.abrir(timeout=30)
    try:
        # A camada de atualização é criada sempre, antes de qualquer rota: o
        # agendador sobe junto com o servidor e a primeira verificação acontece
        # no arranque, não depois de um intervalo inteiro.
        ATU.aplicar_schema(conn)
        # Idem para o acesso: o schema de `usuario`/`sessao` precisa existir
        # antes de qualquer rota, e `_semear` garante a conta de administrador
        # na primeira execução — sem ela, ninguém conseguiria entrar.
        ACESSO.aplicar_schema(conn)
        if ACESSO.limpar_sessoes(conn):
            print("  sessões expiradas removidas")
        if not args.sem_backfill:
            resumo = RD.backfill(conn)
            print(f"  camada editavel: {resumo['org_producao']} orgs de producao + "
                  f"{resumo['certificadas']} certificadas com dado da ANAC")
            if resumo["orgs_sem_pessoa"]:
                print(f"  AVISO {len(resumo['orgs_sem_pessoa'])} org(s) sem vinculo")
    finally:
        conn.close()

    if args.sem_atualizacao:
        print("  atualizacao automatica: desligada por --sem-atualizacao")
    else:
        ATU.iniciar()

    srv = ThreadingHTTPServer((HOST, args.porta), Handler)
    print(f"  registro em http://{HOST}:{args.porta}")
    print("  Ctrl+C para parar")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  encerrado")
    finally:
        ATU.parar()
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
