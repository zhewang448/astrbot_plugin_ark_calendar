"""绘制预缓存调度流程图（启动阶段 + 运行阶段）。"""
from __future__ import annotations
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

FONT_CJK = "Microsoft YaHei"
plt.rcParams["font.family"] = FONT_CJK
plt.rcParams["font.size"] = 9
plt.rcParams["axes.unicode_minus"] = False

C_START  = "#1a3a5c"
C_STEP   = "#2980b9"
C_DECIDE = "#f0a500"
C_WARN   = "#d35400"
C_STOP   = "#c0392b"
C_OK     = "#1e8449"
C_KEY    = "#6c3483"
C_TEAL   = "#16a085"
C_ARROW  = "#2c3e50"
FG_W     = "#ffffff"
FG_D     = "#111111"


def rect(ax, cx, cy, w, h, color, text, fc=FG_W, fs=8.8, radius=0.07):
    box = mpatches.FancyBboxPatch(
        (cx - w / 2, cy - h / 2), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        lw=1.2, edgecolor="#00000033", facecolor=color, zorder=3,
    )
    ax.add_patch(box)
    ax.text(cx, cy, text, ha="center", va="center", fontsize=fs,
            color=fc, zorder=4, multialignment="center", linespacing=1.35,
            fontfamily=FONT_CJK)


def diamond(ax, cx, cy, w, h, text, fs=8.5):
    xs = [cx, cx + w / 2, cx, cx - w / 2, cx]
    ys = [cy + h / 2, cy, cy - h / 2, cy, cy + h / 2]
    ax.fill(xs, ys, color=C_DECIDE, zorder=3, lw=1.2, edgecolor="#00000033")
    ax.text(cx, cy, text, ha="center", va="center", fontsize=fs,
            color=FG_D, zorder=4, multialignment="center", linespacing=1.35,
            fontfamily=FONT_CJK)


def arrow(ax, x0, y0, x1, y1, label="", lside="right", lw=1.8, color=C_ARROW):
    ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="-|>", lw=lw, color=color,
                                connectionstyle="arc3,rad=0.0"), zorder=2)
    if label:
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        dx = 0.16 if lside == "right" else -0.16
        ax.text(mx + dx, my, label, fontsize=8, color="#555",
                ha="left" if lside == "right" else "right", va="center",
                zorder=5, fontfamily=FONT_CJK)


def vline(ax, x, y0, y1, color=C_ARROW, lw=1.8):
    ax.plot([x, x], [y0, y1], color=color, lw=lw, zorder=2)


def hline(ax, x0, x1, y, color=C_ARROW, lw=1.8):
    ax.plot([x0, x1], [y, y], color=color, lw=lw, zorder=2)


def draw_legend(ax, items, x, y):
    ax.text(x, y + 0.5, "图例", fontsize=9, fontweight="bold",
            color="#333", fontfamily=FONT_CJK)
    for i, (c, label) in enumerate(items):
        ry = y - i * 0.52
        b = mpatches.FancyBboxPatch(
            (x, ry - 0.17), 0.45, 0.34,
            boxstyle="round,pad=0,rounding_size=0.04",
            facecolor=c, edgecolor="#00000033", lw=1)
        ax.add_patch(b)
        ax.text(x + 0.62, ry, label, fontsize=8.5, va="center",
                color="#333", fontfamily=FONT_CJK)


def draw_startup():
    """图1：插件启动时 _add_daily_precache_job() 的注册决策。"""
    fig, ax = plt.subplots(figsize=(17, 24))
    ax.set_xlim(0, 17)
    ax.set_ylim(8.5, 33)
    ax.axis("off")
    ax.set_facecolor("#f5f6fa")
    fig.patch.set_facecolor("#f5f6fa")

    ax.text(8.5, 32.3, "每日预缓存任务 —— 注册流程（插件启动阶段）",
            ha="center", fontsize=14, fontweight="bold", color=C_START,
            fontfamily=FONT_CJK)
    ax.text(8.5, 31.8, "main.py  _add_daily_precache_job()",
            ha="center", fontsize=9.5, color="#666", style="italic",
            fontfamily=FONT_CJK)

    CX = 8.5
    W, DW, DH = 4.0, 3.4, 0.95

    rect(ax, CX, 31.1, 3.2, 0.62, C_START, "插件启动", fs=10)

    # 开关判断
    arrow(ax, CX, 30.79, CX, 30.20)
    diamond(ax, CX, 29.72, DW, DH, "daily_precache_enabled\n== false ?")
    arrow(ax, CX + DW / 2, 29.72, 13.0, 29.72, label="Yes →")
    rect(ax, 14.3, 29.72, 2.7, 0.72, C_STOP,
         "[不建任务]\n已禁用  return 0", fs=8.2)
    arrow(ax, CX, 29.24, CX, 28.62, label="No ↓")

    rect(ax, CX, 28.30, W, 0.62, C_STEP, "读取 daily_precache_time 配置")

    # 格式判断
    arrow(ax, CX, 27.99, CX, 27.43)
    diamond(ax, CX, 26.95, DW, DH, "格式合法？\n(HH:MM)")
    arrow(ax, CX - DW / 2, 26.95, 4.1, 26.95, label="No →", lside="left")
    rect(ax, 2.9, 26.95, 2.1, 0.65, C_WARN,
         "[WARNING]\n已忽略无效时间", fs=8.2)
    arrow(ax, 2.9, 26.62, 2.9, 26.10)
    rect(ax, 2.9, 25.80, 2.1, 0.62, C_WARN,
         "回落默认值\n'04:00'", fs=8.2)
    arrow(ax, 3.95, 25.80, 5.3, 25.80)
    hline(ax, 5.3, CX, 25.80)
    arrow(ax, CX, 26.47, CX, 26.12, label="Yes ↓")

    rect(ax, CX, 25.80, W, 0.62, C_STEP, "configured_time 确定")

    arrow(ax, CX, 25.49, CX, 24.92)
    rect(ax, CX, 24.60, W + 0.6, 0.62, C_TEAL,
         "_avoid_report_collision(configured_time)")

    # 日报是否生效
    arrow(ax, CX, 24.29, CX, 23.70)
    diamond(ax, CX, 23.22, 3.9, 0.95,
            "日报已启用\n且有目标 SID？")
    arrow(ax, CX - 1.95, 23.22, 4.5, 23.22, label="No →", lside="left")
    rect(ax, 3.2, 23.22, 2.5, 0.65, C_STEP,
         "report_times = [ ]\n视为不冲突", fs=8.2)
    arrow(ax, 3.2, 22.89, 3.2, 22.40)
    hline(ax, 3.2, CX, 22.40)
    arrow(ax, CX + 1.95, 23.22, 11.9, 23.22, label="Yes →")
    rect(ax, 13.2, 23.22, 2.6, 0.65, C_STEP,
         "report_times =\n日报各时间段", fs=8.2)
    arrow(ax, 13.2, 22.89, 13.2, 22.40)
    hline(ax, 13.2, CX, 22.40)

    # 撞车判断
    arrow(ax, CX, 22.40, CX, 21.85)
    diamond(ax, CX, 21.37, 3.9, 0.95,
            "configured_time\n∈ report_times？")
    arrow(ax, CX + 1.95, 21.37, 11.9, 21.37, label="No →\n不冲突")
    rect(ax, 13.3, 21.37, 2.7, 0.68, C_STEP,
         "scheduled_time =\nconfigured_time\n（原样使用）", fs=8.2)
    arrow(ax, 13.3, 21.03, 13.3, 17.05)
    hline(ax, 13.3, CX, 17.05)

    arrow(ax, CX, 20.89, CX, 20.30, label="Yes ↓\n有冲突")
    rect(ax, CX, 19.98, W + 1.4, 0.68, C_WARN,
         "顺延循环：每次 +10 分钟，最多尝试 6 次\n"
         "PRECACHE_SHIFT_MINUTES = 10   PRECACHE_SHIFT_ATTEMPTS = 6")

    arrow(ax, CX, 19.64, CX, 19.05)
    diamond(ax, CX, 18.57, 3.7, 0.95, "找到不冲突的\n候选时间？")
    arrow(ax, CX - 1.85, 18.57, 4.3, 18.57, label="No →\n6 次全撞", lside="left")
    rect(ax, 2.9, 18.57, 2.6, 0.80, C_STOP,
         "[不建任务]\n全部顺延时段\n都与日报冲突\nreturn 0", fs=8.2)

    arrow(ax, CX, 18.09, CX, 17.50, label="Yes ↓")
    rect(ax, CX, 17.15, W + 1.4, 0.78, C_WARN,
         "[WARNING] 与定时日报冲突，已顺延到 XX:XX 执行\n"
         "scheduled_time = 顺延后时间\n"
         "提示：建议直接把配置改成日报之后的时间")

    # 日切判断（两路汇入）
    arrow(ax, CX, 16.76, CX, 16.20)
    diamond(ax, CX, 15.72, 4.1, 0.95,
            "scheduled_time < 04:00 ?\n（GAME_DAILY_RESET 游戏日切）")
    arrow(ax, CX - 2.05, 15.72, 4.2, 15.72, label="Yes →", lside="left")
    rect(ax, 2.85, 15.72, 2.6, 0.95, C_WARN,
         "[WARNING]\n早于游戏日切 04:00\n当天帮助长图与\n订阅列表会残留\n已结束的活动\n（任务仍会创建）", fs=7.8)
    arrow(ax, 2.85, 15.24, 2.85, 14.60)
    hline(ax, 2.85, CX, 14.60)
    arrow(ax, CX, 15.24, CX, 14.60, label="No ↓\n≥ 04:00")

    rect(ax, CX, 14.25, W + 1.6, 0.78, C_STEP,
         "self._daily_precache_time = scheduled_time\n"
         "scheduler.add_job(_daily_precache, 'cron', hour, minute,\n"
         "    coalesce=True, max_instances=1, misfire_grace_time=600)")

    arrow(ax, CX, 13.86, CX, 13.30)
    rect(ax, CX, 12.95, W + 0.6, 0.70, C_OK,
         "[INFO] 已启用每日预缓存：每天 scheduled_time\n"
         "return 1 —— 任务创建成功")

    draw_legend(ax, [
        (C_START, "起点"),
        (C_STEP, "普通步骤"),
        (C_TEAL, "碰撞处理子流程"),
        (C_DECIDE, "判断分支（菱形）"),
        (C_WARN, "[WARNING] 告警 / 回落 / 顺延"),
        (C_STOP, "[不建任务] 终止路径"),
        (C_OK, "[成功] 任务创建完成"),
    ], x=0.5, y=11.6)

    fig.savefig("precache_startup_flow.png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print("saved: precache_startup_flow.png")


def draw_runtime():
    """图2：cron 触发后 _daily_precache() 的执行路径。"""
    fig, ax = plt.subplots(figsize=(17, 23))
    ax.set_xlim(0, 17)
    ax.set_ylim(8.0, 30)
    ax.axis("off")
    ax.set_facecolor("#f5f6fa")
    fig.patch.set_facecolor("#f5f6fa")

    ax.text(8.5, 29.3, "每日预缓存任务 —— 执行流程（运行时阶段）",
            ha="center", fontsize=14, fontweight="bold", color=C_START,
            fontfamily=FONT_CJK)
    ax.text(8.5, 28.8, "main.py  _daily_precache()",
            ha="center", fontsize=9.5, color="#666", style="italic",
            fontfamily=FONT_CJK)

    CX = 8.5
    W, DW, DH = 4.0, 3.3, 0.92
    EXC_X, EXC_Y = 13.4, 20.75

    rect(ax, CX, 28.10, 3.4, 0.62, C_START,
         "cron 定时触发（Asia/Shanghai）", fs=9.5)

    # 锁判断
    arrow(ax, CX, 27.79, CX, 27.20)
    diamond(ax, CX, 26.72, DW, DH, "_daily_precache_lock\n已被占用？")
    arrow(ax, CX + DW / 2, 26.72, 12.0, 26.72, label="Yes →")
    rect(ax, 13.4, 26.72, 2.8, 0.75, C_STOP,
         "[跳过本次]\n[WARNING] 已有任务\n正在执行  return", fs=8.2)
    arrow(ax, CX, 26.26, CX, 25.68, label="No ↓")

    rect(ax, CX, 25.36, 3.2, 0.62, C_STEP, "获取锁  async with lock")

    # 刷新数据
    arrow(ax, CX, 25.05, CX, 24.48)
    rect(ax, CX, 24.15, W + 1.0, 0.70, C_STEP,
         "snapshot, outcome =\n"
         "await service.snapshot_with_outcome(force=True)\n"
         "强制刷新全部数据源")

    arrow(ax, CX, 23.80, CX, 23.22)
    diamond(ax, CX, 22.74, DW, DH, "抛出异常？")
    arrow(ax, CX + DW / 2, 22.74, 12.0, 22.74, label="Yes →")
    vline(ax, EXC_X, 22.74, EXC_Y + 0.46)
    arrow(ax, CX, 22.28, CX, 21.70, label="No ↓")

    # 渲染日历
    rect(ax, CX, 21.38, W + 0.6, 0.65, C_STEP,
         "_calendar_image(snapshot)\n生成 / 复用当天日历长图")

    arrow(ax, CX, 21.05, CX, 20.48)
    diamond(ax, CX, 20.00, DW, DH, "抛出异常？")
    arrow(ax, CX + DW / 2, 20.00, 12.0, 20.00, label="Yes →")
    vline(ax, EXC_X, 20.00, EXC_Y - 0.46)

    # 异常处理块
    rect(ax, EXC_X, EXC_Y, 2.9, 0.92, C_STOP,
         "[异常处理]\nexcept Exception:\n"
         "[ERROR] 预缓存执行失败\n"
         "_notify_admin(...)\n"
         "「预缓存未能完成」", fs=7.8)

    arrow(ax, CX, 19.54, CX, 18.98, label="No ↓")

    # 关键步骤
    rect(ax, CX, 18.60, W + 2.0, 0.82, C_KEY,
         "[关键步骤] self.help_cache.invalidate()\n"
         "清除当天全部帮助长图缓存（full / subscribe）\n"
         "→ 这是帮助图当天唯一的重渲染点，修复凌晨生成的旧图")

    arrow(ax, CX, 18.19, CX, 17.62)
    rect(ax, CX, 17.30, W + 1.2, 0.68, C_STEP,
         "for mode in HelpImageCache.MODES:\n"
         "    await self._render_help_image(mode, snapshot)")

    arrow(ax, CX, 16.96, CX, 16.40)
    diamond(ax, CX, 15.92, 3.6, 0.90, "单个 mode\n渲染结果？")

    # 分支 A：成功且缓存命中
    arrow(ax, CX - 1.8, 15.92, 4.4, 15.92,
          label="渲染成功\n缓存写入成功", lside="left")
    rect(ax, 3.0, 15.92, 2.7, 0.75, C_OK,
         "[OK]\nhelp_cache_paths[mode]\n计入完成列表", fs=8.2)
    arrow(ax, 3.0, 15.55, 3.0, 14.35)
    hline(ax, 3.0, CX, 14.35)

    # 分支 B：成功但缓存写失败
    arrow(ax, CX + 1.8, 15.92, 11.7, 15.92,
          label="渲染成功\n但缓存写入失败")
    rect(ax, 13.2, 15.92, 2.9, 0.75, C_WARN,
         "[WARNING]\nuncached_modes\n后续命令时重试", fs=8.2)
    arrow(ax, 13.2, 15.55, 13.2, 14.35)
    hline(ax, 13.2, CX, 14.35)

    # 分支 C：渲染失败
    arrow(ax, CX, 15.47, CX, 14.98, label="渲染失败\n返回 None ↓")
    rect(ax, CX, 14.62, W + 1.4, 0.78, C_STOP,
         "[ERROR] failed_modes += mode\n"
         "记录日志，收到对应命令时按需重渲染\n"
         "注意：不通知管理员，不影响日报投递")

    arrow(ax, CX, 14.23, CX, 13.70)

    # 健康检查
    rect(ax, CX, 13.35, W + 1.2, 0.70, C_STEP,
         "await self._observe_health(outcome, '每日预缓存')\n"
         "只依据本次 outcome 判定数据源异常")

    arrow(ax, CX, 13.00, CX, 12.42)
    diamond(ax, CX, 11.94, 4.2, 0.92,
            "本次刷新存在数据源异常\n且未在告警冷却期内？")
    arrow(ax, CX - 2.1, 11.94, 4.2, 11.94, label="Yes →", lside="left")
    rect(ax, 2.85, 11.94, 2.6, 0.78, C_WARN,
         "_notify_admin\n数据源异常告警\n（按 event_key 去重\n+ 冷却时间过滤）", fs=8.0)
    arrow(ax, 2.85, 11.55, 2.85, 10.95)
    hline(ax, 2.85, CX, 10.95)
    arrow(ax, CX, 11.48, CX, 10.95, label="No ↓")

    rect(ax, CX, 10.60, W + 1.2, 0.72, C_OK,
         "[完成] 每日预缓存正常结束\n"
         "日历长图缓存就绪 + 帮助长图已按新快照刷新")

    draw_legend(ax, [
        (C_START, "起点 / 定时触发"),
        (C_STEP, "普通执行步骤"),
        (C_KEY, "[关键步骤] 帮助图缓存清理"),
        (C_DECIDE, "判断分支（菱形）"),
        (C_WARN, "[WARNING] 告警 / 降级路径"),
        (C_STOP, "[ERROR/跳过] 错误与终止路径"),
        (C_OK, "[OK/完成] 成功路径"),
    ], x=0.5, y=9.6)

    fig.savefig("precache_runtime_flow.png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close(fig)
    print("saved: precache_runtime_flow.png")


if __name__ == "__main__":
    draw_startup()
    draw_runtime()
    print("done.")
