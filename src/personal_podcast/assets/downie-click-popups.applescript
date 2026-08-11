	on run argv
	-- 弹窗自动处理(兼容有无弹窗 + 重复下载判断):
	--   1. 系统「未设定打开 url 的应用程序」弹窗(CoreServicesUIAgent)→ 点【取消】
	--   2. Downie「已下载过,重新下载?」弹窗(跳过/下载 二选一):
	--      - 有源文件(reuse) → 点【跳过】(默认, 避免重复下载)
	--      - 无源文件(redownload) → 点【下载】(文件丢失需重新下载)
	--   3. Downie 播放视频弹窗 → 点【完成】(否则不触发下载)
	--   4. 无弹窗 → 静默返回 "无弹窗", 不影响正常下载流程
	-- 用法: osascript downie-click-popups.applescript [reuse|redownload]
	-- 需要辅助功能权限: 系统设置 → 隐私与安全性 → 辅助功能 → 勾选运行终端
	set mode to "reuse"
	if (count of argv) > 0 then
		set mode to (item 1 of argv) as text
	end if

	tell application "System Events"
		-- 1. 系统级「选取应用程序」弹窗 → 取消
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
										return "已点击系统弹窗【取消】"
									end if
								end if
							end try
						end repeat
					end try
				end repeat
			end tell
		end try

		-- 2. Downie 弹窗
		try
			tell process "Downie 4"
				set frontmost to true
				delay 0.3
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

				-- 2a. 先处理「已下载过,重新下载?」弹窗(跳过/下载 二选一)
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
									return "已点击【" & target & "】(" & mode & ")"
								end if
							end repeat
						end if
					end try
				end repeat

				-- 2b. 播放视频弹窗 → 点【完成】(优先), 其次 确定/OK/Done/好的
				--     注意: 此阶段不再包含「下载」, 避免误触发重复下载
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
									return "已点击【" & target & "】按钮"
								end if
							end repeat
						end if
					end try
				end repeat
			end tell
		end try

		return "无弹窗"
	end tell
end run
