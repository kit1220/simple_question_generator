import glob, json, os, random, re, sys, threading, webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from openai import OpenAI
import config

client = OpenAI(api_key=config.API_KEY, base_url=config.BASE_URL)
STATE = "state.json"
QUESTIONS = []
SERVER = None
SUBMITTED = False


def load_state():
    if not os.path.exists(STATE):
        return {"courses": {}, "files": {}}
    raw = json.load(open(STATE, encoding="utf-8"))
    if "files" in raw:
        return raw
    return {"courses": raw, "files": {}}


state = load_state()

PAGE = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<title>測驗</title>
<script id="MathJax-script" async
  src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
body{font-family:system-ui,sans-serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.8}
.card{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}
.q,.opt,.exp{white-space:pre-wrap}
.opt{display:block;margin-left:1rem}
pre{background:#f6f8fa;border:1px solid #ddd;border-radius:6px;padding:.8rem;overflow-x:auto;
    white-space:pre;line-height:1.5;margin:.6rem 0}
code{font-family:Consolas,"Courier New",monospace;font-size:.95em}
:not(pre)>code{background:#eef;padding:.1rem .3rem;border-radius:4px}
textarea{width:100%;box-sizing:border-box;padding:.4rem;font-family:Consolas,monospace}
.ok{color:#0a7d2c;font-weight:bold}.bad{color:#c0392b;font-weight:bold}
.tag{color:#666;font-size:.9rem}.exp{color:#333;margin-top:.4rem}
button{padding:.6rem 1.5rem;font-size:1rem;cursor:pointer}
#score{font-size:1.3rem;font-weight:bold}
</style></head><body>
<h2>📚 測驗</h2><div id="score"></div>
<div id="qs"></div>
<button id="submit">交卷</button>
<script>
const Q = __DATA__;
const box = document.getElementById("qs");
function typeset(){ if(window.MathJax && MathJax.typesetPromise) MathJax.typesetPromise(); }
function inline(el, s){
  s.split(/`([^`\n]+)`/).forEach((p,i)=>{
    if(i%2===1){ const c=document.createElement("code"); c.textContent=p; el.append(c); }
    else if(p){ el.append(document.createTextNode(p)); }
  });
}
function rich(el, text){
  String(text).split(/```[^\n]*\n?([\s\S]*?)```/).forEach((p,i)=>{
    if(i%2===1){
      const pre=document.createElement("pre"), code=document.createElement("code");
      code.textContent=p.replace(/\n+$/,""); pre.append(code); el.append(pre);
    } else if(p){ inline(el, p.replace(/^\n+|\n+$/g,"")); }
  });
}
Q.forEach((q,i)=>{
  const c=document.createElement("div"); c.className="card";
  const tag=document.createElement("div"); tag.className="tag";
  tag.textContent="Q"+(i+1)+"  ["+q.course+"]  "+(q.type==="mcq"?"選擇題":"簡答題");
  const t=document.createElement("div"); t.className="q"; rich(t,q.q);
  c.append(tag,t);
  if(q.type==="mcq"){
    (q.options||[]).forEach(o=>{
      const l=document.createElement("label"); l.className="opt";
      const r=document.createElement("input"); r.type="radio"; r.name="a"+q.id; r.value=o.trim()[0];
      l.append(r, document.createTextNode(" ")); rich(l,o); c.append(l);
    });
  } else {
    const a=document.createElement("textarea"); a.rows=3; a.id="a"+q.id; c.append(a);
  }
  const res=document.createElement("div"); res.id="r"+q.id; c.append(res);
  box.append(c);
});
typeset();
document.getElementById("submit").onclick = async function(){
  this.disabled=true; this.textContent="批改中...";
  const answers={};
  Q.forEach(q=>{
    if(q.type==="mcq"){
      const s=document.querySelector("input[name=a"+q.id+"]:checked");
      answers[q.id]=s?s.value:"";
    } else answers[q.id]=document.getElementById("a"+q.id).value;
  });
  const resp=await fetch("/submit",{method:"POST",body:JSON.stringify({answers})});
  const data=await resp.json();
  let score=0;
  Q.forEach(q=>{
    const r=data.results[q.id]; if(r.ok) score++;
    const d=document.getElementById("r"+q.id);
    const m=document.createElement("div"); m.className=r.ok?"ok":"bad";
    if(r.ok){ m.textContent="✅ 正確"; }
    else { m.append(document.createTextNode("❌ 錯誤,標準答案:")); rich(m,r.answer); }
    d.append(m);
    if(r.feedback){ const f=document.createElement("div"); f.className="exp";
      f.append(document.createTextNode("回饋:")); rich(f,r.feedback); d.append(f); }
    const e=document.createElement("div"); e.className="exp";
    e.append(document.createTextNode("解釋:")); rich(e,r.explain); d.append(e);
  });
  document.querySelectorAll("input,textarea").forEach(x=>x.disabled=true);
  document.getElementById("score").textContent="得分:"+score+" / "+Q.length;
  this.textContent="已交卷"; window.scrollTo(0,0); typeset();
};
</script></body></html>"""

PROMPT = r"""你是大學「__COURSE__」課程的導師。根據下面講義片段出 __N__ 題,混合概念題與計算/程式題,只考講義內容,題目要彼此不同。
用繁體中文。數學公式用 LaTeX:行內公式用 \( ... \),獨立公式用 \[ ... \],不要用單個 $。
程式碼必須放進 ``` 程式碼區塊,保留真實換行與縮排;行內程式碼用單個反引號。
不要輸出 JSON,每題嚴格使用以下格式:
<question>
<type>mcq 或 short</type>
<text>題目內容</text>
<option>A. ...</option>
<option>B. ...</option>
<source>這題依據的片段開頭【】內的檔名,必須與片段標示完全一致</source>
<answer>mcq 只寫字母;short 寫標準答案</answer>
<explain>簡短解釋</explain>
</question>
(short 題不要寫 option;mcq 題要有 2 到 4 個 option)
講義片段:
__TEXT__"""


def ask_text(prompt, max_tokens, retries=2):
    for _ in range(retries + 1):
        r = client.chat.completions.create(
            model=config.MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            extra_body={"thinking": {"type": "disabled"}},
        )
        c = r.choices[0]
        if c.finish_reason == "length":
            max_tokens = min(max_tokens * 2, 8000)
            continue
        return c.message.content or ""
    sys.exit("輸出多次被截斷,請降低題數")


def tag(name, s):
    m = re.search(rf"<{name}>(.*?)</{name}>", s, re.S)
    return m.group(1).strip() if m else ""


def parse_questions(raw):
    out = []
    for block in re.findall(r"<question>(.*?)</question>", raw, re.S):
        opts = [o.strip() for o in re.findall(r"<option>(.*?)</option>", block, re.S)]
        q = {"q": tag("text", block), "options": opts,
             "type": "mcq" if tag("type", block).lower() == "mcq" and len(opts) >= 2 else "short",
             "source": tag("source", block),
             "answer": tag("answer", block), "explain": tag("explain", block)}
        if q["q"] and q["answer"]:
            out.append(q)
    return out


def list_courses():
    if not os.path.isdir("notes"):
        sys.exit("找不到 notes/ 資料夾")
    return sorted(d for d in os.listdir("notes")
                  if glob.glob(os.path.join("notes", d, "*.md")))


def course_chunks(course, size=3000):
    out = []
    for f in glob.glob(os.path.join("notes", course, "*.md")):
        text = open(f, encoding="utf-8").read()
        buf = ""
        for part in re.split(r"(?m)^(?=#{1,3} )", text):
            for i in range(0, len(part), size):
                piece = part[i:i + size]
                if buf and len(buf) + len(piece) > size:
                    out.append((os.path.basename(f), buf))
                    buf = ""
                buf += piece
        if buf.strip():
            out.append((os.path.basename(f), buf))
    return out


def file_weight(course, name):
    s = state["files"].get(f"{course}/{name}", {"right": 0, "wrong": 0})
    n = s["right"] + s["wrong"]
    return (s["wrong"] + 1) / (n + 2) + 0.1


def weighted_sample(course, chunks, k=3):
    count = {}
    for name, _ in chunks:
        count[name] = count.get(name, 0) + 1
    pool, picked = list(chunks), []
    for _ in range(min(k, len(pool))):
        w = [file_weight(course, n) / count[n] for n, _ in pool]
        c = random.choices(pool, weights=w)[0]
        pool.remove(c)
        picked.append(c)
    return picked


def gen_batch(course, n, chunks):
    raw = ""
    for _ in range(3):
        picked = weighted_sample(course, chunks)
        names = {nm for nm, _ in picked}
        text = "\n\n".join(f"【{name}】\n{c}" for name, c in picked)
        prompt = (PROMPT.replace("__COURSE__", course)
                  .replace("__N__", str(n)).replace("__TEXT__", text))
        raw = ask_text(prompt, min(8000, 900 * n + 1500))
        qs = parse_questions(raw)
        if qs:
            for q in qs:
                q["course"] = course
                q["source"] = q.get("source", "").strip("【】 ")
                if q["source"] not in names:
                    q["source"] = ""
            return qs
    print("---- 最後一次原始輸出 ----")
    print(raw[:1500])
    sys.exit("無法解析模型輸出")


def gen_course(course, n):
    chunks = course_chunks(course)
    out = []
    while len(out) < n:
        out += gen_batch(course, min(5, n - len(out)), chunks)
    return out[:n]


def grade(q, user):
    if q["type"] == "mcq":
        ok = bool(user) and user.strip().upper()[:1] == str(q["answer"]).strip().upper()[:1]
        return ok, ""
    if not user.strip():
        return False, "沒有作答"
    prompt = (f"題目:{q['q']}\n標準答案:{q['answer']}\n學生答案:{user}\n"
              "判斷學生是否大致正確。只輸出:<correct>yes 或 no</correct><feedback>一兩句回饋</feedback>")
    raw = ask_text(prompt, 600)
    return tag("correct", raw).lower().startswith("y"), tag("feedback", raw)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        public = [{k: q[k] for k in ("id", "course", "q", "type", "options") if k in q}
                  for q in QUESTIONS]
        data = json.dumps(public, ensure_ascii=True).replace("</", "<\\/")
        self._send(200, PAGE.replace("__DATA__", data), "text/html")

    def do_POST(self):
        global SUBMITTED
        if self.path != "/submit" or SUBMITTED:
            return self._send(400, "{}", "application/json")
        SUBMITTED = True
        n = int(self.headers.get("Content-Length", 0))
        answers = json.loads(self.rfile.read(n))["answers"]
        with ThreadPoolExecutor(max_workers=4) as ex:
            graded = list(ex.map(lambda q: grade(q, answers.get(str(q["id"]), "")), QUESTIONS))
        results, score = {}, 0
        for q, (ok, fb) in zip(QUESTIONS, graded):
            key = "right" if ok else "wrong"
            c = state["courses"].setdefault(q["course"], {"right": 0, "wrong": 0})
            c[key] += 1
            if q.get("source"):
                f = state["files"].setdefault(f"{q['course']}/{q['source']}",
                                              {"right": 0, "wrong": 0})
                f[key] += 1
            score += ok
            results[q["id"]] = {"ok": ok, "answer": q["answer"],
                                "explain": q.get("explain", ""), "feedback": fb}
        json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        self._send(200, json.dumps({"results": results}, ensure_ascii=False), "application/json")
        print(f"\n已交卷,得分:{score}/{len(QUESTIONS)}")
        threading.Thread(target=SERVER.shutdown, daemon=True).start()


def parse_choice(s, total):
    s = s.strip().lower()
    if s == "all":
        return list(range(total))
    try:
        idx = [int(x) for x in re.split(r"[,\s,]+", s) if x]
    except ValueError:
        return None
    if not idx or any(i < 0 or i >= total for i in idx):
        return None
    return list(dict.fromkeys(idx))


def main():
    global SERVER
    courses = list_courses()
    if not courses:
        sys.exit("notes/ 底下沒有含 .md 的課程資料夾")
    print("目前的課程:")
    for i, c in enumerate(courses):
        print(f"  {i}: {c}  ({len(glob.glob(os.path.join('notes', c, '*.md')))} 份講義)")

    while True:
        sel = parse_choice(input("\n要考哪些課程?輸入編號(可多選,例如 0,1;all 代表全部): "), len(courses))
        if sel is not None:
            break
        print("輸入無效,請再試一次。")
    while True:
        try:
            per = int(input("每個課程要幾題? "))
            if per > 0:
                break
        except ValueError:
            pass
        print("請輸入正整數。")
    chosen = [courses[i] for i in sel]

    print("\n各講義目前表現(權重越高越常出現):")
    for c in chosen:
        for name in sorted({n for n, _ in course_chunks(c)}):
            s = state["files"].get(f"{c}/{name}", {"right": 0, "wrong": 0})
            print(f"  {c}/{name}: ✅{s['right']} ❌{s['wrong']}  權重 {file_weight(c, name):.2f}")

    print(f"\n共 {len(chosen)} 個課程 × {per} 題 = {per * len(chosen)} 題,出題中...")
    with ThreadPoolExecutor(max_workers=len(chosen)) as ex:
        groups = list(ex.map(lambda c: gen_course(c, per), chosen))
    for q in (q for g in groups for q in g):
        q["id"] = len(QUESTIONS)
        QUESTIONS.append(q)

    port = 8765
    try:
        SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        SERVER = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = SERVER.server_address[1]
    url = "".join(["ht", "tp", ":", "//", "127.0.0.1:", str(port), "/"])
    print("題目已準備好,埠號:", port)
    print("若瀏覽器沒有自動打開,請在網址列輸入:127.0.0.1:" + str(port))
    print(url)
    webbrowser.open(url)
    try:
        SERVER.serve_forever()
    except KeyboardInterrupt:
        print("\n已中止")


if __name__ == "__main__":
    main()