	-- 弹窗自动处理·常驻循环版(兼容有无弹窗 + 多弹窗 + 零延迟):
	--   每 0.2 秒扫描一次, 发现弹窗立即处理, 基本在播放出声前关闭:
	--   1. 系统「未设定打开 url 的应用程序」弹窗(CoreServicesUIAgent)→ 点【取消】
	--   2. Downie「已下载过,重新下载?」弹窗(跳过/下载 二选一)
	--   3. Downie 播放视频弹窗 → 立即点【完成】(否则不触发下载且会出声)
	--   4. 无弹窗 → 静默继续扫描
	-- 用法: osascript downie-click-popups-loop.applescript [reuse|redownload] [maxSeconds]
	--   由调用方(Python 后台线程)启动, 下载完成/超时后 terminate 该进程
	-- 需要辅助功能权限: 系统设置 → 隐私与安全性 → 辅助功能 → 勾选运行终端

	on handleSystemPopup()
		tell application "System Events"
			try
				tell process "CoreServicesUIAgent"
					repeat with w in (every window)
						try
							set els to entire contents of w
							repeat with el in els
								try
									if (role of el as text) is "AXButton" then
										set t to ""
										set d to ""
										try
											set t to (title of el as text)
										end try
										try
											set d to (description of el as text)
										end try
										if t is "取消" or d is "取消" then
											click el
											return true
										end if
									end if
								end try
							end repeat
						end try
					end repeat
				end tell
			end try
		end tell
		return false
	end handleSystemPopup

	on handleDowniePopup(mode)
		tell application "System Events"
			try
				tell process "Downie 4"
					set els to {}
					try
						set els to entire contents of window 1
					on error
						repeat with w in (every window)
							try
								set els to els & (entire contents of w)
							end try
						end repeat
					end try

					-- 2a. 「已下载过,重新下载?」弹窗(跳过/下载 二选一)
					if mode is "redownload" then
						set targets to {"下载", "重新下载", "Download", "Redownload"}
					else
						set targets to {"跳过", "Skip", "取消", "Cancel", "不用", "不要"}
					end if
					repeat with el in els
						try
							if (role of el as text) is "AXButton" then
								set t to ""
								set d to ""
								try
									set t to (title of el as text)
								end try
								try
									set d to (description of el as text)
								end try
								repeat with target in targets
									if (t is target) or (d is target) or (d contains target) then
										click el
										return true
									end if
								end repeat
							end if
						end try
					end repeat

					-- 2b. 播放视频弹窗 → 立即点【完成】(优先), 其次 确定/OK/Done/好的
					set targets to {"完成", "确定", "OK", "Done", "好的"}
					repeat with el in els
						try
							if (role of el as text) is "AXButton" then
								set t to ""
								set d to ""
								try
									set t to (title of el as text)
								end try
								try
									set d to (description of el as text)
								end try
								repeat with target in targets
									if (t is target) or (d is target) or (d contains target) then
										click el
										return true
									end if
								end repeat
							end if
						end try
					end repeat

					-- 2c. 「考虑登录」等站点登录提示 → 点【考虑登录】继续(2026-08-11 实测)
					set targets to {"考虑登录"}
					repeat with el in els
						try
							if (role of el as text) is "AXButton" then
								set t to ""
								set d to ""
								try
									set t to (title of el as text)
								end try
								try
									set d to (description of el as text)
								end try
								repeat with target in targets
									if (t is target) or (d is target) or (d contains target) then
										click el
										return true
									end if
								end repeat
							end if
						end try
					end repeat
				end tell
			end try
		end tell
		return false
	end handleDowniePopup

	on run argv
		set mode to "reuse"
		if (count of argv) > 0 then
			set mode to (item 1 of argv) as text
		end if
		set maxSeconds to 1800
		if (count of argv) > 1 then
			try
				set maxSeconds to (item 2 of argv) as integer
			end try
		end if

		set startTime to (current date)
		repeat
			-- 先快速扫描一次(不做 frontmost/delay, 尽快点掉播放窗口)
			if handleSystemPopup() then
				delay 0.2
			else if handleDowniePopup(mode) then
				delay 0.2
			else
				delay 0.15
			end if
			if ((current date) - startTime) > maxSeconds then
				return "超时退出"
			end if
		end repeat
	end run
