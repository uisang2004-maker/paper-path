"""로그인한 사용자별 저장소(C): 브라우저 저장(A)과 같은 내용을 Supabase(Postgres)에도 두어 다른 PC에서 이어 본다.

- 로그인은 선택이다. 로그인하지 않으면 지금처럼 이 브라우저(A)·결과 파일(B)에만 남고 서버에는 저장하지 않는다.
- 사용자는 이메일이 아니라 로그인 ID(sub)를 비밀 소금(USER_ID_SALT)과 섞은 해시로만 구분한다(이메일·이름은 저장 안 함).
- 저장 형식은 src/saved.py와 같다.
  · 결과 항목: 글 해시 → {"t": 번역, "g": 뜻 풀이, "ts"}  (paperpath_items)
  · 다시 열 문서: 문서 id → {종류, 제목, 절 글자, …}     (paperpath_docs, PDF 파일·표 이미지는 없음)
- Supabase는 REST(PostgREST)로 부르고, 서버 전용 service_role 키는 Streamlit 비밀값에만 둔다.
  테이블 SQL과 설정 순서는 docs/LOGIN_SETUP.md에 있다(행 수준 보안을 켜고 정책을 두지 않아 이 키로만 접근).
"""

import hashlib
import hmac
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import certifi

ITEMS = "paperpath_items"
DOCS = "paperpath_docs"
USAGE = "paperpath_usage"      # 사용자별 하루 번역 기록(src/quota.py)
BATCH = 50                     # 한 번에 올릴 행 수


class StoreError(Exception):
    pass


def user_key(sub: str, salt: str) -> str:
    """로그인 ID → 저장소에서 쓰는 사용자 키(되돌릴 수 없는 해시)."""
    return hmac.new(salt.encode("utf-8"), sub.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


class MemoryStore:
    """테스트용(서버 메모리). SupabaseStore와 같은 함수를 가진다."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}
        self.docs: dict[tuple[str, str], dict] = {}
        self.usage: dict[tuple[str, str], dict] = {}

    def get_usage(self, user: str, day: str) -> dict:
        return json.loads(json.dumps(self.usage.get((user, day), {})))

    def put_usage(self, user: str, day: str, usage: dict) -> None:
        self.usage[(user, day)] = json.loads(json.dumps(usage))

    def load_all(self, user: str) -> tuple[dict, dict]:
        return ({k: json.loads(json.dumps(v)) for (u, k), v in self.items.items() if u == user},
                {k: json.loads(json.dumps(v)) for (u, k), v in self.docs.items() if u == user})

    def put_items(self, user: str, items: dict) -> None:
        for k, v in items.items():
            self.items[(user, k)] = json.loads(json.dumps(v))

    def put_docs(self, user: str, docs: dict) -> None:
        for k, v in docs.items():
            self.docs[(user, k)] = json.loads(json.dumps(v))

    def delete_doc(self, user: str, doc_id: str) -> None:
        self.docs.pop((user, doc_id), None)

    def delete_all(self, user: str) -> None:
        for d in (self.items, self.docs):
            for k in [k for k in d if k[0] == user]:
                del d[k]


class SupabaseStore:
    def __init__(self, url: str, key: str, timeout: float = 10.0):
        self.base = url.rstrip("/") + "/rest/v1/"
        self.key = key
        self.timeout = timeout
        self.ctx = ssl.create_default_context(cafile=certifi.where())

    def _req(self, method: str, table: str, query: dict | None = None, body=None, prefer: str = "") -> list:
        url = self.base + table + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"apikey": self.key, "Content-Type": "application/json"}
        if self.key.startswith("eyJ"):      # 예전 형식(JWT) service_role 키만 Authorization에도 넣는다(새 sb_secret_ 키는 apikey만)
            headers["Authorization"] = f"Bearer {self.key}"
        if prefer:
            headers["Prefer"] = prefer
        data = None
        if body is not None:
            # Postgres jsonb는 NUL 문자(\u0000)를 받지 않는다(PDF 글자에 섞여 들어올 수 있음) → 지우고 보낸다
            data = json.dumps(body, ensure_ascii=False).replace("\\u0000", "").encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            detail = ""
            try:                                  # Supabase가 돌려준 오류 이유(키 값은 들어 있지 않다)
                err = json.loads(e.read() or b"{}")
                detail = " · " + str(err.get("message") or err.get("msg") or err.get("hint") or "")[:160]
            except Exception:
                pass
            raise StoreError(f"내 계정 저장소 오류(HTTP {e.code}{detail}). 결과는 이 브라우저에는 그대로 저장됩니다.") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise StoreError("내 계정 저장소에 연결하지 못했습니다. 결과는 이 브라우저에는 그대로 저장됩니다.") from e
        return json.loads(raw) if raw else []

    def _upsert(self, table: str, key_col: str, val_col: str, user: str, rows: dict) -> None:
        body = [{"user_id": user, key_col: k, val_col: v} for k, v in rows.items()]
        for i in range(0, len(body), BATCH):
            self._req("POST", table, body=body[i:i + BATCH], prefer="resolution=merge-duplicates,return=minimal")

    def load_all(self, user: str) -> tuple[dict, dict]:
        items = self._req("GET", ITEMS, {"user_id": f"eq.{user}", "select": "text_hash,item"})
        docs = self._req("GET", DOCS, {"user_id": f"eq.{user}", "select": "doc_id,doc"})
        return {r["text_hash"]: r["item"] for r in items}, {r["doc_id"]: r["doc"] for r in docs}

    def put_items(self, user: str, items: dict) -> None:
        self._upsert(ITEMS, "text_hash", "item", user, items)

    def put_docs(self, user: str, docs: dict) -> None:
        self._upsert(DOCS, "doc_id", "doc", user, docs)

    def delete_doc(self, user: str, doc_id: str) -> None:
        self._req("DELETE", DOCS, {"user_id": f"eq.{user}", "doc_id": f"eq.{doc_id}"})

    def get_usage(self, user: str, day: str) -> dict:
        rows = self._req("GET", USAGE, {"user_id": f"eq.{user}", "day": f"eq.{day}", "select": "usage"})
        return rows[0]["usage"] if rows else {}

    def put_usage(self, user: str, day: str, usage: dict) -> None:
        self._req("POST", USAGE, body=[{"user_id": user, "day": day, "usage": usage}],
                  prefer="resolution=merge-duplicates,return=minimal")

    def delete_all(self, user: str) -> None:
        self._req("DELETE", ITEMS, {"user_id": f"eq.{user}"})
        self._req("DELETE", DOCS, {"user_id": f"eq.{user}"})
