	-- 弹窗自动处理·修正版v2(2026-08-11 23:30):
	--   核心: 深层遍历(entire contents)Downie 所有窗口按钮, 过滤正常窗口按钮,
	--   只有发现「弹窗特征按钮」才动作; 无弹窗完全静默, 不按任何键。
	--   背景: 之前用浅层 buttons of window 抓不到深层弹窗按钮,
	--   导致播放弹窗没点【完成】→ 下载不触发(踩坑记录早有此条)。
	-- 处理规则:
	--   1. 系统弹窗(CoreServicesUIAgent)→ 点【取消】
	--   2. Downie 内「下载/重新下载」(redownload)或「跳过」(reuse)→ 按键
	--   3. 播放视频弹窗 → 点【完成】(出声前关闭, 不点不触发下载)
	--   4. 「考虑登录」等登录提示 → 点【考虑登录】继续
	--   5. 无弹窗按钮 → 静默等待
	-- 用法: osascript downie-click-popups-loop.applescript [reuse|redownload] [maxSeconds]

	-- Downie 正常窗口的按钮(不属弹窗), 出现这些不处理
	on isNormalButton(d)
		if d is "关闭按钮" or d is "全屏幕按钮" or d is "最小化按钮" or d is "缩放按钮" then
			return true
		end if
		if d contains "显示历史记录" or d contains "搜索和热门下载" or d contains "清空已完成的下载" then
			return true
		end if
		if d contains "联系支持" or d contains "添加..." or d contains "打开用户自定义提取" then
			return true
		end if
		return false
	end isNormalButton

	-- 深层遍历 Downie 按钮, 返回弹窗特征按钮文本(无弹窗返回 "")
	on downiePopupButtons()
		tell application "System Events"
			tell process "Downie 4"
				set out to ""
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
									if not my isNormalButton(d) then
										if t is not "" or d is not "" then
											set out to out & t & "|" & d & linefeed
										end if
									end if
								end if
							end try
						end repeat
					end try
				end repeat
				return out
			end tell
		end tell
	end downiePopupButtons

	-- 系统弹窗取消
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
			-- 1. 系统弹窗 → 点取消
			if handleSystemPopup() then
				delay 0.2
			else
				-- 2. Downie 弹窗: 深层遍历, 确认有弹窗特征按钮才动作
				set btns to downiePopupButtons()
				set handled to false
				if btns is not "" then
					-- 2a. 重复下载(跳过/下载)
					if mode is "redownload" then
						if btns contains "下载" or btns contains "重新下载" or btns contains "Download" or btns contains "Redownload" then
							try
								tell application "Downie 4" to activate
								delay 0.1
								tell application "System Events" to keystroke return
							end try
							set handled to true
						end if
					else
						if btns contains "跳过" or btns contains "Skip" or btns contains "不用" then
							try
								tell application "Downie 4" to activate
								delay 0.1
								tell application "System Events" to keystroke return
							end try
							set handled to true
						end if
					end if
					-- 2b. 播放视频/完成类(不点不触发下载)
					if not handled and (btns contains "完成" or btns contains "确定" or btns contains "OK" or btns contains "Done" or btns contains "好的") then
						try
							tell application "Downie 4" to activate
							delay 0.1
							tell application "System Events" to keystroke return
						end try
						set handled to true
					end if
					-- 2c. 考虑登录
					if not handled and btns contains "考虑登录" then
						try
							tell application "Downie 4" to activate
							delay 0.1
							tell application "System Events" to keystroke return
						end try
						set handled to true
					end if
				end if
				if handled then
					delay 0.2
				else
					delay 0.4
				end if
			end if
			if ((current date) - startTime) > maxSeconds then
				return "超时退出"
			end if
		end repeat
	end run
