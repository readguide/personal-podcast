	-- 弹窗自动处理·极速版(兼容有无弹窗 + 多弹窗 + 零延迟):
	--   核心思路: 不遍历 UI 树(entire contents 太慢, 要几秒),
	--   改用 Downie 自身 AppleScript 接口(0.02s)快速检测窗口变化,
	--   发现弹窗立即按回车触发「默认选中按钮」(macOS 弹窗默认按钮都响应回车)。
	--   兜底: 若回车无效, 再遍历按钮名单点击。
	-- 用法: osascript downie-click-popups-loop.applescript [reuse|redownload] [maxSeconds]
	--   由调用方(Python 后台线程)启动, 下载完成/超时后 terminate 该进程
	-- 需要辅助功能权限: 系统设置 → 隐私与安全性 → 辅助功能 → 勾选运行终端

	on isDownieRunning()
		try
			tell application "System Events"
				set p to (name of every process)
			end tell
			repeat with n in p
				if (n as text) is "Downie 4" then
					return true
				end if
			end repeat
		end try
		return false
	end isDownieRunning

	-- 快速取 Downie 窗口名列表(Downie 自身接口, ~0.02s)
	on downieWindows()
		try
			tell application "Downie 4"
				return (name of every window) as list
			end tell
		on error
			return {}
		end try
	end downieWindows

	-- 快速路径: 发现弹窗(窗口集合变化/数量异常) → 回车触发默认按钮
	-- 返回 {是否处理, 当前窗口列表}
	on fastReturn(prevWindows, initialized)
		set cur to downieWindows()
		if (count of cur) is 0 then
			return {false, prevWindows}
		end if
		if not initialized then
			-- 首次运行: 只记录窗口基线, 不按回车(避免误触发)
			return {false, cur}
		end if
		set changed to false
		-- 窗口名列表变化, 或窗口数量比上次多 → 可能有新弹窗
		if (count of cur) is not (count of prevWindows) then
			set changed to true
		else
			try
				repeat with i from 1 to (count of cur)
					if (item i of cur) is not (item i of prevWindows) then
						set changed to true
						exit repeat
					end if
				end repeat
			end try
		end if
		if changed then
			-- 激活 Downie 并按回车(触发默认按钮: 完成/考虑登录/确定 等)
			try
				tell application "Downie 4" to activate
				delay 0.15
				tell application "System Events" to keystroke return
			end try
			return {true, cur}
		end if
		return {false, cur}
	end fastReturn

	-- 兜底: 遍历按钮名单点击(entire contents 慢, 只在回车无效时用)
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

					-- 通用策略: 优先点「默认选中」按钮(focused/highlighted)
					repeat with el in els
						try
							if (role of el as text) is "AXButton" then
								set foc to false
								set hlt to false
								try
									set foc to (focused of el) as boolean
								end try
								try
									set hlt to (highlighted of el) as boolean
								end try
								if foc or hlt then
									click el
									return true
								end if
							end if
						end try
					end repeat

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

					-- 2b. 播放视频弹窗 → 点【完成】
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

					-- 2c. 「考虑登录」等站点登录提示 → 点【考虑登录】继续
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
		set prevWindows to {}
		set initialized to false
		repeat
			-- 快速路径: 检测窗口变化 → 回车
			set r to fastReturn(prevWindows, initialized)
			set handled to (item 1 of r)
			set prevWindows to (item 2 of r)
			set initialized to true
			if handled then
				delay 0.15
			else
				-- 慢路径兜底(低频): 系统弹窗取消 + Downie 按钮名单
				if handleSystemPopup() then
					delay 0.15
				else if handleDowniePopup(mode) then
					delay 0.15
				else
					delay 0.3
				end if
			end if
			if ((current date) - startTime) > maxSeconds then
				return "超时退出"
			end if
		end repeat
	end run
