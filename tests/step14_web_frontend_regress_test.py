"""Step14 验证：前端行为回归（第二轮修订 P1 的浏览器侧修复）。

覆盖审计矩阵 T11 / T12 及"揭晓后不二次计 view"的前端部分。
本项目没有浏览器测试框架，这里做两件事：
  1. `node --check` 保证 app.js 语法可运行（无 Node 时跳过并提示）；
  2. 对关键处理逻辑做源级结构断言——断言"Cancel 在任何 api 调用之前 return"
     "私人编辑按返回 state 分流""揭晓直接渲染返回 DTO"这些行为契约存在。
服务端对应的语义（offer 只在显式调用时创建、编辑不改状态、揭晓恰好 1 次
view）已由 Step4 / Step5 / Step13 在服务层证明。

运行：python tests/step14_web_frontend_regress_test.py"""
import re
import shutil
import subprocess

from _util import ROOT  # noqa: F401

APP_JS = ROOT / "app" / "web" / "app.js"
src = APP_JS.read_text(encoding="utf-8")


def must(cond, what):
    assert cond, f"前端回归失败：{what}"
    print(f"  [PASS] {what}")


# ---- 语法可运行 ----
node = shutil.which("node")
if node:
    subprocess.run([node, "--check", str(APP_JS)], check=True)
    must(True, "app.js 通过 node --check 语法检查")
else:
    print("  [SKIP] 本机没有 Node，跳过语法检查（结构断言仍然执行）")

# ---- T11：递星附言按 Cancel 必须立即 return，不产生 POST /offers ----
offer_block = re.search(
    r'action === "offer-private"\)\s*\{(.*?)\n  \}', src, re.S)
must(offer_block is not None, "offer-private 分支存在")
block = offer_block.group(1)
prompt_i = block.find("window.prompt")
null_check = re.search(r'if \(raw === null\) return;', block)
api_i = block.find('api("/offers"')
must(prompt_i != -1 and api_i != -1, "prompt 与 /offers 调用都在分支内")
must(null_check is not None, "Cancel 判断使用原始返回值 raw === null")
must(null_check.start() > prompt_i and null_check.start() < api_i,
    "Cancel 判断位于 prompt 之后、POST /offers 之前——按 Cancel 不会发出任何请求")
must("?? \"\"" not in block and "?? ''" not in block,
    "不再用 ?? 空串吞掉 null（那正是旧 bug 的根源）")

# ---- T12：私人星编辑后按返回 state 分流，不请求 shared 详情 ----
onwrite = re.search(r"async function onWrite\(event\).*?\n\}", src, re.S)
must(onwrite is not None, "onWrite 函数存在")
w = onwrite.group(0)
must('saved.state === "SHARED"' in w,
    "编辑保存后按服务端返回的 state 分流")
must('switchView("observatory", { reload: false })' in w,
    "私人星（SEALED）编辑后回到三瓶视图，不调用任何 shared 详情接口")
# 复审回归：编辑公共星保存后直接用 PATCH 返回值更新当前详情，
# 不重新 GET 详情（保存动作不算一次新的查看）
must("state.selectedShared = { ...state.selectedShared, ...saved }" in w,
    "编辑公共星后用 PATCH 返回字段就地更新 selectedShared")
must("renderInspector(state.selectedShared)" in w,
    "就地重渲染详情，不重新请求")
must(w.count("openSharedStar") == 1,
    "openSharedStar 只保留一个兜底跳转（仅当选中的不是当前详情/用户主动查看才计足迹）")
fallback = re.search(r'openSharedStar\([^{]*\{ navigate: true \}', w)
must(fallback is not None, "兜底跳转仅在详情面板未选中这颗星时发生")

# ---- 复审回归：回应提交后 append 返回的回应，不重新 GET 详情 ----
submit_block = re.search(r"async function submitResponse\(event\).*?\n\}", src, re.S)
must(submit_block is not None, "submitResponse 函数存在")
sr = submit_block.group(0)
must("openSharedStar" not in sr,
    "回应后不再调用 openSharedStar——用户没有重新点开这颗星")
must("state.selectedShared.responses = [" in sr and "renderInspector" in sr,
    "直接把 POST 返回的回应 append 到当前详情并重渲染")

# ---- 揭晓后直接渲染返回 DTO，不补发会计 view 的详情请求 ----
must("function showRevealedStar(star)" in src, "showRevealedStar 直接渲染揭晓 DTO")
for scene in ("request-open", "offer-accept", "session-take"):
    seg = re.search(r'action === "' + scene + r'".{0,500}', src, re.S)
    must(seg is not None, f"{scene} 分支存在")
    body = seg.group(0)
    must("showRevealedStar(star);" in body,
         f"{scene} 揭晓后改用 showRevealedStar 渲染返回 DTO")
    must("openSharedStar(star.id)" not in body,
         f"{scene} 场景内没有补发详情请求（不会再多计一次 view）")

# ---- 纪念日 / 自定义特殊日分别呈现、发起必须绑定日期 ----
must('group("纪念日", anniversaries)' in src
     and 'group("自定义特殊日", customs)' in src,
    "日期选择器按纪念日 / 自定义特殊日分组呈现")
must("session-start-special" not in src,
    "旧的'按特殊日发起'错配入口已移除")
start_block = re.search(r'action === "session-start"\)\s*\{(.*?)\n    \}', src, re.S)
must(start_block is not None, "session-start 分支存在")
sb = start_block.group(1)
must("special_date_id: select.value" in sb, "发起请求携带具体 special_date_id")
must('picked.type === "anniversary" ? "anniversary" : "special_day"' in sb,
     "前端 type 由所选日期的数据派生（服务端还会再校验一致性）")

# ---- 详情展示首次查看 ----
must("first_view_at" in src and "首次查看" in src,
    "详情面板展示首次查看日期（只记录一次的那条）")

# ---- 私人星显式查看入口存在且走专用端点 ----
must("view-private" in src and "/stars/hidden/mine/${encodeURIComponent(id)}" in src,
    "私人星有显式'查看详情'入口，调用私人详情端点（列表刷新不计足迹）")

print("STEP 14 PASS —— Cancel 不发请求 / 私人编辑不跳 shared / 揭晓不二次计 view / "
      "纪念日与自定义特殊日区分呈现")
