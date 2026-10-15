#!/usr/bin/env bash
# ============================================================
# 游侠出行 · 演示数据复现命令
#
# 用途：把「故事线 B 强化版」演示中用到的 flyai 查询原样列出来，
#      任何人都能在本地跑一遍，核对智能体当时拿到的是不是真实数据。
#
# 前置：
#   npm i -g @fly-ai/flyai-cli@1.0.16
#   flyai config set FLYAI_API_KEY <你的Key>      # 申请：https://flyai.open.fliggy.com/
#
# 注意：
#   1. 飞猪有 QPS 限制（约 2）与基于行为的风控，**不要把这个脚本改成循环刷**，
#      否则会触发 HTTP 451，且同一 Key 一天内多次被封冷却时间会递增。
#      脚本已在每条命令之间加了 sleep。
#   2. 价格 / 余票 / 场次随时效变化，下面的「参考值」是 2026-10-15 实测快照，
#      跑出来不一样是正常的，以实际返回为准。
#   3. search-poi 的 --category 必须用中文枚举值，英文值会报错。
# ============================================================
set -uo pipefail

STEP=${1:-all}
GAP=3   # 请求间隔秒，别调小

hr() { printf '\n%s\n' "------------------------------------------------------------"; }
run() {
  echo "+ $*"
  eval "$@"
  sleep "$GAP"
}

echo "游侠出行 · 演示数据复现（步进：$STEP）"
echo "间隔 ${GAP}s —— 请勿调快，会触发风控"

if [ "$STEP" = "all" ] || [ "$STEP" = "1" ]; then
  hr; echo "第 1 幕 · 重庆 → 成都 去程车次"
  echo "  参考值（2026-10-15）：D49 ¥85 / Z333 ¥50.5 / T8804 ¥50.5"
  run "flyai search-train --from 重庆北 --to 成都西 --date 2026-10-17"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "2" ]; then
  hr; echo "第 1 幕 · 成都 → 重庆 回程车次"
  echo "  注意：火车数据有方向性差异，去程回程必须分方向各查一次，不能复用。"
  run "flyai search-train --from 成都西 --to 重庆北 --date 2026-10-19"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "3" ]; then
  hr; echo "第 4 幕 · 成都电竞酒店"
  echo "  参考值（2026-10-15）：雷泽电竞酒店 ¥29/晚 / 758 电竞酒店 ¥55/晚"
  run "flyai search-hotel --city 成都 --keyword 电竞"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "4" ]; then
  hr; echo "第 5 幕 · 东安湖体育公园（场馆事实 + 赛事供给）"
  echo "  参考值（2026-10-15）：2021 大运会主场馆 / 4 万座 / 龙泉驿区"
  echo "  注：--category 必须用中文枚举值"
  run "flyai search-poi --city 成都 --category 体育场馆"
  hr; echo "  · 赛事门票（演示中此处真实返回「无赛事」——这是兜底触发点）"
  run "flyai keyword-search --keyword '成都东安湖体育公园 比赛 演唱会 门票'"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "5" ]; then
  hr; echo "第 6 幕 · Z 字头直达火车（数据真实性铁证）"
  echo "  参考值（2026-10-15）：去程真实 Z 字头为 Z333（¥50.5），回程 Z588（¥50.5）"
  echo "  演示中用户问的是 Z587，智能体主动纠正「Z587 方向反了」。"
  echo "  如果价格是模型编的，它不会去纠正用户给的车次号。"
  run "flyai search-train --from 重庆北 --to 成都西 --date 2026-10-17"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "6" ]; then
  hr; echo "第 2 幕 · 本周末成都赛事（兜底触发点 ①）"
  run "flyai keyword-search --keyword '成都 电竞赛事 门票'"
fi

if [ "$STEP" = "all" ] || [ "$STEP" = "7" ]; then
  hr; echo "第 6 幕 · 顺路玩（POI）"
  run "flyai search-poi --city 成都 --category 主题乐园"
fi

hr
echo "完成。核对要点："
echo "  1. 车次号、票价是否为真实返回（不是「¥1xx」这类体验模式模糊值）"
echo "  2. 结果里是否出现「体验模式」字样 —— 出现说明 Key 没生效，走的是公共试用额度"
echo "  3. 是否返回 router.feizhu.com 的真实预订链接"
echo
echo "数据真实性逐条核验见：demo/verification-report.md"
echo "逐幕完整流程见：demo/session-transcript.md"
