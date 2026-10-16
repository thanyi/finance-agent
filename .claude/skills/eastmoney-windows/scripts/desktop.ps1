param([Parameter(Mandatory=$true)][string]$RequestJson)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$request = $RequestJson | ConvertFrom-Json
$deadline = [Diagnostics.Stopwatch]::StartNew()
$script:draftWritten = $false

Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class EMDesktop {
 public delegate bool Callback(IntPtr h, IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct RECT {public int Left,Top,Right,Bottom;}
 [StructLayout(LayoutKind.Sequential)] public struct POINT {public int X,Y; public POINT(int x,int y){X=x;Y=y;}}
 [StructLayout(LayoutKind.Sequential)] public struct GUIINFO {public int cbSize;public uint flags;public IntPtr active,focus,capture,menuOwner,moveSize,caret;public RECT caretRect;}
 [DllImport("user32.dll")] public static extern bool EnumWindows(Callback cb,IntPtr l);
 [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr h,Callback cb,IntPtr l);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h,StringBuilder b,int n);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,StringBuilder b,int n);
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
 [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] public static extern IntPtr GetDlgItem(IntPtr h,int id);
 [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h,int n);
 [DllImport("user32.dll")] public static extern int GetDlgCtrlID(IntPtr h);
 [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread,ref GUIINFO info);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr extra);
 [DllImport("user32.dll")] public static extern uint GetDpiForWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern int GetSystemMetrics(int n);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,IntPtr w,string l,uint f,uint t,out IntPtr r);
 [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern IntPtr SendMessageTimeout(IntPtr h,uint m,IntPtr w,StringBuilder l,uint f,uint t,out IntPtr r);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h,uint m,IntPtr w,IntPtr l);
 public static List<long> Handles(IntPtr parent) {
  var result=new List<long>();
  Callback callback=(h,l)=>{result.Add(h.ToInt64());return true;};
  if(parent==IntPtr.Zero) EnumWindows(callback,IntPtr.Zero); else EnumChildWindows(parent,callback,IntPtr.Zero);
  return result;
 }
 public static string Class(IntPtr h){var b=new StringBuilder(256);GetClassName(h,b,256);return b.ToString();}
 public static uint Owner(IntPtr h){uint p;GetWindowThreadProcessId(h,out p);return p;}
 public static GUIINFO FocusInfo(){uint p;uint t=GetWindowThreadProcessId(GetForegroundWindow(),out p);var i=new GUIINFO();i.cbSize=Marshal.SizeOf(i);if(!GetGUIThreadInfo(t,ref i))throw new Exception("focus_failed|Cannot inspect foreground focus");return i;}
 public static string Caption(IntPtr h){var b=new StringBuilder(1024);GetWindowText(h,b,1024);return b.ToString();}
 public static string Text(IntPtr h){
  if(Class(h)=="Edit" && (GetWindowLong(h,-16)&0x20)!=0) return "[PASSWORD]";
  var b=new StringBuilder(2048);IntPtr result;
  if(SendMessageTimeout(h,0x000D,(IntPtr)2048,b,2,500,out result)==IntPtr.Zero) throw new Exception("control_timeout|Control read timed out");
  return b.ToString();
 }
 public static void SetText(IntPtr h,string text){
  if(Class(h)!="Edit" || (GetWindowLong(h,-16)&0x20)!=0) throw new Exception("unexpected_control|Expected non-password Edit");
  IntPtr result;
  if(SendMessageTimeout(h,0x000C,IntPtr.Zero,text,2,1000,out result)==IntPtr.Zero) throw new Exception("control_timeout|Control write timed out");
 }
}
'@
[void][EMDesktop]::SetThreadDpiAwarenessContext([IntPtr](-4))

function Check-Deadline {
 if ($deadline.Elapsed.TotalSeconds -gt 50) { throw 'desktop_timeout|Desktop operation deadline reached' }
}
function Rect-Of([IntPtr]$handle) {
 $r=[EMDesktop+RECT]::new()
 if (-not [EMDesktop]::GetWindowRect($handle,[ref]$r)) { throw 'window_disappeared|Cannot read window geometry' }
 return @{left=$r.Left;top=$r.Top;width=($r.Right-$r.Left);height=($r.Bottom-$r.Top)}
}
function Window-Info([IntPtr]$handle) {
 return @{handle=$handle.ToInt64();pid=[EMDesktop]::Owner($handle);title=([EMDesktop]::Caption($handle) -replace '\d{8,}','[MASKED]');class=[EMDesktop]::Class($handle);visible=[EMDesktop]::IsWindowVisible($handle);rect=(Rect-Of $handle);dpi=[EMDesktop]::GetDpiForWindow($handle)}
}
function Discover {
 $session=[Diagnostics.Process]::GetCurrentProcess().SessionId
 $processes=@(Get-Process mainfree -ErrorAction SilentlyContinue | Where-Object {$_.SessionId -eq $session})
 if ($request.client_pid) { $processes=@($processes | Where-Object {$_.Id -eq $request.client_pid}) }
 if ($processes.Count -gt 1) { throw 'client_selection_required|Multiple clients; select --client-pid' }
 $script:client=if ($processes.Count -eq 1) {$processes[0]} else {$null}
 $script:windows=@()
 if ($script:client) {
  foreach ($handle in [EMDesktop]::Handles([IntPtr]::Zero)) {
   if ([EMDesktop]::Owner([IntPtr]$handle) -eq $script:client.Id) { $script:windows+=,(Window-Info ([IntPtr]$handle)) }
  }
  foreach ($top in @($script:windows)) {
   foreach($child in [EMDesktop]::Handles([IntPtr]$top.handle)) {
    if ([EMDesktop]::Class([IntPtr]$child) -eq '#32770' -and [EMDesktop]::Caption([IntPtr]$child) -eq '东方财富证券' -and $child -notin @($script:windows | ForEach-Object {$_.handle})) {$script:windows+=,(Window-Info ([IntPtr]$child))}
   }
  }
 }
 $trades=@($script:windows | Where-Object {$_.visible -and $_.title -eq '东方财富证券' -and $_.class -eq '#32770'})
 if ($trades.Count -gt 1) { throw 'client_selection_required|Multiple visible trading windows' }
 $script:trade=if($trades.Count -eq 1){[IntPtr]$trades[0].handle}else{[IntPtr]::Zero}
}
function Installed-Clients {
 $paths=@()
 $keys=@('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*','HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*')
 foreach ($entry in (Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -match 'Eastmoney|东方财富'})) {
  if ($entry.DisplayIcon -match '^"?(.+?\.exe)') {$paths+=$matches[1]}
  if ($entry.InstallLocation) {$paths+=(Join-Path $entry.InstallLocation 'mainfree.exe')}
 }
 return @($paths | Where-Object {(Split-Path $_ -Leaf) -eq 'mainfree.exe' -and (Test-Path -LiteralPath $_)} | Select-Object -Unique)
}
function Has-Login-Controls {
 if (-not $script:client) { return $false }
 foreach ($w in $script:windows) {
  if (-not $w.visible) {continue}
  if ($w.title -match '登录|登陆') {return $true}
  foreach ($child in [EMDesktop]::Handles([IntPtr]$w.handle)) {
   if ([EMDesktop]::IsWindowVisible([IntPtr]$child) -and [EMDesktop]::Class([IntPtr]$child) -eq 'Edit' -and ([EMDesktop]::GetWindowLong([IntPtr]$child,-16) -band 0x20)) {return $true}
  }
 }
 return $false
}
function Blocking-Dialogs {
 return @($script:windows | Where-Object {$_.visible -and $_.title -ne '东方财富终端' -and $_.handle -ne $script:trade.ToInt64() -and -not ($_.class -like 'Afx:*' -and $_.title -eq '')})
}
function Doctor {
 Discover
 $state=if(-not $script:client){'client_not_running'}elseif(Has-Login-Controls){'auth_required'}elseif($script:trade -eq [IntPtr]::Zero -and $request.auth_required){'auth_required'}elseif($script:trade -eq [IntPtr]::Zero){'trade_unavailable'}elseif(-not [EMDesktop]::IsWindowEnabled($script:trade) -or @(Blocking-Dialogs).Count -gt 0){'unexpected_dialog'}else{'ready'}
 $clientInfo=if($script:client){@{pid=$script:client.Id;path=$script:client.Path;version=$script:client.FileVersion;session=$script:client.SessionId}}else{$null}
 $tradeInfo=if($script:trade -ne [IntPtr]::Zero){Window-Info $script:trade}else{$null}
 $hiddenTrades=@($script:windows | Where-Object {$_.title -eq '东方财富证券' -and $_.class -eq '#32770' -and [EMDesktop]::Class([EMDesktop]::GetDlgItem([IntPtr]$_.handle,1002)) -eq 'ContainerWnd_DC'})
 $children=@()
 foreach($w in $script:windows){if($w.title -eq '东方财富终端'){
  foreach($h in [EMDesktop]::Handles([IntPtr]$w.handle)){
   $class=[EMDesktop]::Class([IntPtr]$h)
   $children+=@{handle=$h;class=$class;visible=[EMDesktop]::IsWindowVisible([IntPtr]$h);caption=([EMDesktop]::Caption([IntPtr]$h) -replace '\d{8,}','[MASKED]')}
  }
 }}
 $tradeChildren=@()
 if($script:trade -ne [IntPtr]::Zero){foreach($h in [EMDesktop]::Handles($script:trade)){$tradeChildren+=@{handle=$h;class=[EMDesktop]::Class([IntPtr]$h);id=[EMDesktop]::GetDlgCtrlID([IntPtr]$h);visible=[EMDesktop]::IsWindowVisible([IntPtr]$h);rect=(Rect-Of ([IntPtr]$h))}}}
 $focus=[EMDesktop]::FocusInfo()
 return @{status=$state;client=$clientInfo;trade=$tradeInfo;trade_children=$tradeChildren;focus=@{handle=$focus.focus.ToInt64();class=[EMDesktop]::Class($focus.focus);caret=$focus.caret.ToInt64();caret_class=[EMDesktop]::Class($focus.caret);caret_rect=@{left=$focus.caretRect.Left;top=$focus.caretRect.Top;right=$focus.caretRect.Right;bottom=$focus.caretRect.Bottom}};native_children=$children;hidden_trade_candidates=$hiddenTrades;trading_candidates=@($script:windows | Where-Object {$_.title -eq '东方财富证券'});windows=@($script:windows | Where-Object {$_.visible});installed_clients=@(Installed-Clients);screen=@{width=[EMDesktop]::GetSystemMetrics(0);height=[EMDesktop]::GetSystemMetrics(1)}}
}
function Require-Trade {
 Discover
 if (Has-Login-Controls) {throw 'auth_required|Trading login is required'}
 if (-not $script:client) {throw 'client_not_running|Trading client is not running'}
 if ($script:trade -eq [IntPtr]::Zero -and $request.auth_required) {throw 'auth_required|Trading was explicitly logged out; reauthenticate and review the account'}
 if ($script:trade -eq [IntPtr]::Zero) {throw 'trade_unavailable|Open the securities trading window'}
 if (-not [EMDesktop]::IsWindowEnabled($script:trade)) {throw 'unexpected_dialog|Trading window is disabled by a modal dialog'}
 if (@(Blocking-Dialogs).Count -gt 0) {throw 'unexpected_dialog|Resolve the visible client dialog before proceeding'}
 $profile=$request.profile
 if (-not $profile -or -not $profile.reviewed -or -not $profile.screenshot_sha256) {throw 'calibration_required|Review a screenshot and calibrate the current layout'}
 $rect=Rect-Of $script:trade
 if ($script:client.FileVersion -ne $profile.version -or [EMDesktop]::GetDpiForWindow($script:trade) -ne $profile.dpi -or $rect.width -ne $profile.width -or $rect.height -ne $profile.height) {throw 'layout_changed|Client version, DPI or window size changed; recalibrate'}
 if ($rect.left -lt 0 -or $rect.top -lt 0 -or ($rect.left+$rect.width) -gt [EMDesktop]::GetSystemMetrics(0) -or ($rect.top+$rect.height) -gt [EMDesktop]::GetSystemMetrics(1)) {throw 'layout_changed|Trading window must be fully on the primary desktop'}
 foreach ($w in $script:windows) {
  if ($w.visible -and $w.class -eq '#32770' -and $w.handle -ne $script:trade.ToInt64()) {throw 'unexpected_dialog|Resolve the visible client dialog before proceeding'}
 }
 $shell=New-Object -ComObject WScript.Shell
 if (-not $shell.AppActivate($script:client.Id)) {throw 'focus_failed|Cannot activate client'}
 Start-Sleep -Milliseconds 150
 if ([EMDesktop]::Owner([EMDesktop]::GetForegroundWindow()) -ne $script:client.Id) {throw 'focus_failed|Client did not receive foreground focus'}
 $script:bounds=$rect
}
function Point-For([string]$tag) {
 $allowed=@('refresh','grid','buy_nav','sell_nav','code','price','quantity','funds','logout')
 if ($tag -notin $allowed) {throw 'unsupported_action|Only named non-submit targets are allowed'}
 $anchor=$request.profile.anchors.$tag
 if (-not $anchor -or $anchor.Count -ne 2) {throw 'calibration_required|Named target is not calibrated'}
 $x=[double]$anchor[0];$y=[double]$anchor[1]
 if ($x -le 0 -or $x -ge 1 -or $y -le 0 -or $y -ge 1) {throw 'invalid_anchor|Target must be inside the client'}
 # Entire final-submit/clear-button row is excluded. Fields are cleared by Edit messages.
 if ($x -ge 0.20 -and $x -le 0.57 -and $y -ge 0.53 -and $y -le 0.65) {throw 'forbidden_target|Final-submit row is not an allowed target'}
 return @([int]($script:bounds.left+$script:bounds.width*$x),[int]($script:bounds.top+$script:bounds.height*$y))
}
function Click-Point([int[]]$point,[bool]$right=$false,[bool]$reviewedExitConfirmation=$false) {
 Check-Deadline
 if ($script:trade -ne [IntPtr]::Zero -and -not [EMDesktop]::IsWindowEnabled($script:trade) -and -not $reviewedExitConfirmation) {throw 'unexpected_dialog|A modal dialog appeared before the next click'}
 if ([EMDesktop]::Owner([EMDesktop]::GetForegroundWindow()) -ne $script:client.Id -or [EMDesktop]::Owner([EMDesktop]::WindowFromPoint([EMDesktop+POINT]::new($point[0],$point[1]))) -ne $script:client.Id) {throw 'focus_failed|Click target is covered or client lost focus'}
 [void][EMDesktop]::SetCursorPos($point[0],$point[1])
 if ($right) {[EMDesktop]::mouse_event(8,0,0,0,[UIntPtr]::Zero);[EMDesktop]::mouse_event(16,0,0,0,[UIntPtr]::Zero)}
 else {[EMDesktop]::mouse_event(2,0,0,0,[UIntPtr]::Zero);[EMDesktop]::mouse_event(4,0,0,0,[UIntPtr]::Zero)}
 Start-Sleep -Milliseconds 150
}
function Click-Target([string]$tag) {Click-Point (Point-For $tag)}
function Export-Dialog {
 $found=@()
 foreach ($h in [EMDesktop]::Handles([IntPtr]::Zero)) {
  if ([EMDesktop]::Owner([IntPtr]$h) -eq $script:client.Id -and [EMDesktop]::IsWindowVisible([IntPtr]$h) -and [EMDesktop]::Caption([IntPtr]$h) -eq '导出对话框') {$found+=[IntPtr]$h}
 }
 if($found.Count -ne 1){throw 'export_dialog_missing|Expected one export wizard'}
 return $found[0]
}
function Export-Button([IntPtr]$dialog,[int]$id,[string[]]$labels) {
 $button=[EMDesktop]::GetDlgItem($dialog,$id)
 if ($button -eq [IntPtr]::Zero -or [EMDesktop]::Owner($button) -ne $script:client.Id -or [EMDesktop]::Class($button) -ne 'Button' -or [EMDesktop]::Text($button) -notin $labels -or -not [EMDesktop]::IsWindowEnabled($button)) {throw 'unexpected_export_control|Export button identity or state changed'}
 [void][EMDesktop]::PostMessage($button,0x00F5,[IntPtr]::Zero,[IntPtr]::Zero)
 Start-Sleep -Milliseconds 200
}
function Capture-Export {
 Require-Trade
 if (Test-Path -LiteralPath $request.output_file) {throw 'output_exists|Refusing to overwrite an existing export'}
 Click-Target 'refresh'
 Start-Sleep -Milliseconds 250
 $point=Point-For 'grid'
 Click-Point $point $true
 $offset=$request.profile.export_menu_offset
 if (-not $offset -or $offset.Count -ne 2) {throw 'calibration_required|Export menu offset missing'}
 Click-Point @([int]($point[0]+$offset[0]),[int]($point[1]+$offset[1]))
 $dialog=Export-Dialog
 $edit=[EMDesktop]::GetDlgItem($dialog,1002)
 if ($edit -eq [IntPtr]::Zero) {throw 'unexpected_export_control|Export filename control missing'}
 $combo=[EMDesktop]::GetDlgItem($dialog,20095)
 if ([EMDesktop]::Text($combo) -notmatch 'XLSX') {throw 'unsupported_export_format|Select the XLSX export format'}
 [EMDesktop]::SetText($edit,$request.output_file)
 if ([EMDesktop]::Text($edit) -ne $request.output_file) {throw 'filename_mismatch|Export filename readback differs'}
 Export-Button $dialog 20092 @('下一步')
 if ([EMDesktop]::Class([EMDesktop]::GetDlgItem($dialog,20100)) -ne 'SysTreeView32') {throw 'unexpected_export_control|Expected export field selection page'}
 Export-Button $dialog 20097 @('全选')
 Export-Button $dialog 20092 @('下一步')
 while (-not (Test-Path -LiteralPath $request.output_file) -or [EMDesktop]::Text([EMDesktop]::GetDlgItem($dialog,20092)) -ne '完成') {Check-Deadline;Start-Sleep -Milliseconds 200}
 Export-Button $dialog 20092 @('完成')
 return @{status='ok';operation='snapshot';client_version=$script:client.FileVersion;refreshed=$true;output_file=$request.output_file}
}
function Edit-For([string]$tag) {
 $point=Point-For $tag
 Click-Point $point
 $edits=@()
 foreach ($h in [EMDesktop]::Handles($script:trade)) {
  $handle=[IntPtr]$h
  if (-not [EMDesktop]::IsWindowVisible($handle) -or [EMDesktop]::Class($handle) -ne 'Edit') {continue}
  $r=Rect-Of $handle
  if ($point[0] -ge $r.left -and $point[0] -lt ($r.left+$r.width) -and $point[1] -ge $r.top -and $point[1] -lt ($r.top+$r.height) -and -not ([EMDesktop]::GetWindowLong($handle,-16) -band 0x20)) {$edits+=$handle}
 }
 if ($edits.Count -ne 1) {throw 'form_not_readable|No unambiguous editable control at the calibrated field'}
 return $edits[0]
}
function Clear-Form {
 $values=@{}
 foreach($tag in @('quantity','price','code')) {
  $edit=Edit-For $tag
  [EMDesktop]::SetText($edit,'')
  $values[$tag]=[EMDesktop]::Text($edit)
  if ($values[$tag] -ne '') {throw 'draft_cleanup_failed|A draft field could not be cleared'}
 }
 foreach($tag in @('quantity','price','code')) {
  if ([EMDesktop]::Text((Edit-For $tag)) -ne '') {throw 'draft_cleanup_failed|A field was repopulated after leaving it'}
 }
 $script:draftWritten=$false
 return $values
}
function Form-Check([bool]$clearOnly=$false) {
 Require-Trade
 if ($clearOnly) {return @{status='ok';operation='form_clear';values=(Clear-Form)}}
 if ($request.side -notin @('buy','sell') -or $request.symbol -notmatch '^\d{6}$' -or $request.quantity -notmatch '^\d+$' -or [decimal]$request.quantity -le 0 -or $request.price -notmatch '^\d+(\.\d+)?$' -or [decimal]$request.price -le 0) {throw 'invalid_form_request|Invalid non-submitting form fields'}
 Click-Target ($request.side+'_nav')
 foreach($tag in @('code','price','quantity')) {
  $existing=[EMDesktop]::Text((Edit-For $tag))
  if ($existing -ne '') {throw 'existing_draft|An existing draft is present; it has not been changed'}
 }
 $readback=@{}
 try {
  foreach($entry in @(@{tag='code';value=$request.symbol},@{tag='price';value=$request.price},@{tag='quantity';value=[string]$request.quantity})) {
   $edit=Edit-For $entry.tag
   [EMDesktop]::SetText($edit,[string]$entry.value)
   $script:draftWritten=$true
   if ([EMDesktop]::Text($edit) -ne [string]$entry.value) {throw 'form_readback_mismatch|Written field did not read back'}
  }
  foreach($tag in @('code','price','quantity')) {$readback[$tag]=[EMDesktop]::Text((Edit-For $tag))}
  if ($readback.code -ne $request.symbol -or [decimal]$readback.price -ne [decimal]$request.price -or [decimal]$readback.quantity -ne [decimal]$request.quantity) {throw 'form_readback_mismatch|Form values changed after leaving the fields'}
 } catch {
  $original=$_.Exception.Message
  try {$null=Clear-Form} catch {throw ('draft_cleanup_failed|'+$original+'; cleanup: '+$_.Exception.Message)}
  throw $original
 }
 return @{status='ok';operation='form_filled';side=$request.side;readback=$readback;submitted=$false;draft_requires_cleanup=$true}
}
function Dismiss-Ipo {
 Discover
 $review=$request.dismiss_review
 $dialogs=@($script:windows | Where-Object {$_.visible -and $_.class -eq 'TradeIpoDuiDlg' -and $_.title -eq 'TradeIpoDuiDlg'})
 if ($dialogs.Count -ne 1 -or -not $review.screenshot_sha256 -or -not $script:client -or $script:client.FileVersion -ne $review.version) {throw 'unexpected_dialog|Expected the reviewed Today Subscription dialog'}
 $dialog=$dialogs[0]
 if ($dialog.handle -ne $review.handle -or $dialog.pid -ne $review.pid -or $dialog.dpi -ne $review.dpi -or $dialog.rect.width -ne $review.rect.width -or $dialog.rect.height -ne $review.rect.height) {throw 'unexpected_dialog|The reviewed subscription dialog has changed'}
 [void][EMDesktop]::PostMessage([IntPtr]$dialog.handle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
 Start-Sleep -Milliseconds 300
 Discover
 if (@($script:windows | Where-Object {$_.visible -and $_.handle -eq $dialog.handle}).Count -gt 0) {throw 'dialog_close_failed|Subscription dialog did not close; inspect it manually'}
 return (Doctor)
}
function Start-Client {
 Discover
 if ($script:client) {return (Doctor)}
 $paths=@(Installed-Clients)
 if ($request.client_exe) {$paths=@($request.client_exe)}
 if ($paths.Count -ne 1 -or (Split-Path $paths[0] -Leaf) -ne 'mainfree.exe' -or -not (Test-Path -LiteralPath $paths[0])) {throw 'client_selection_required|Specify one installed mainfree.exe'}
 Start-Process -FilePath $paths[0] | Out-Null
 Start-Sleep -Seconds 2
 return (Doctor)
}
function Restart-Client {
 Discover
 if (-not $script:client) {return (Start-Client)}
 $path=$script:client.Path
 $old=$script:client.Id
 $main=@($script:windows | Where-Object {$_.visible -and $_.title -eq '东方财富终端'})
 if ($main.Count -ne 1) {throw 'restart_blocked|Cannot identify one main client window'}
 if ($request.confirmation_review) {
  $review=$request.confirmation_review
  $dialogs=@($script:windows | Where-Object {$_.visible -and $_.class -eq '#32770' -and $_.title -eq '提示'})
  if ($dialogs.Count -ne 1 -or -not $review.screenshot_sha256 -or $review.class -ne '#32770' -or $review.title -ne '提示' -or $dialogs[0].handle -ne $review.handle -or $dialogs[0].pid -ne $review.pid -or $dialogs[0].rect.width -ne $review.width -or $dialogs[0].rect.height -ne $review.height -or $dialogs[0].dpi -ne $review.dpi -or $script:client.FileVersion -ne $request.profile.version) {throw 'unexpected_dialog|The reviewed continue-exit promotion dialog has changed'}
  $r=$dialogs[0].rect
  $point=@([int]($r.left+$r.width*0.5),[int]($r.top+$r.height*0.895))
  $target=[EMDesktop]::WindowFromPoint([EMDesktop+POINT]::new($point[0],$point[1]))
  if ($target.ToInt64() -ne $review.handle -and $target.ToInt64() -notin @([EMDesktop]::Handles([IntPtr]$review.handle))) {throw 'unexpected_dialog|Continue-exit target is obscured'}
  Click-Point $point $false $true
 } else {
  [void][EMDesktop]::PostMessage([IntPtr]$main[0].handle,0x0010,[IntPtr]::Zero,[IntPtr]::Zero)
 }
 $wait=[Diagnostics.Stopwatch]::StartNew()
 while ((Get-Process -Id $old -ErrorAction SilentlyContinue) -and $wait.Elapsed.TotalSeconds -lt 6) {Start-Sleep -Milliseconds 250}
 if (Get-Process -Id $old -ErrorAction SilentlyContinue) {throw 'restart_pending|Client did not exit normally; inspect its confirmation or tray behavior'}
 Start-Process -FilePath $path | Out-Null
 $request.client_pid=$null
 Start-Sleep -Seconds 3
 $result=Doctor
 $result.previous_pid=$old
 $result.restarted=$true
 return $result
}
function Confirm-Logout {
 Discover
 $review=$request.confirmation_review
 if (-not $review -or -not $review.screenshot_sha256) {throw 'screenshot_review_required|Review the exact exit-trading confirmation first'}
 $dialogs=@($script:windows | Where-Object {$_.visible -and $_.class -eq 'EMCustomTipDlg'})
 if ($dialogs.Count -ne 1 -or $dialogs[0].handle -ne $review.handle -or $dialogs[0].pid -ne $review.pid -or $dialogs[0].rect.width -ne $review.width -or $dialogs[0].rect.height -ne $review.height) {throw 'unexpected_dialog|The reviewed exit confirmation has changed'}
 if ([EMDesktop]::Owner([EMDesktop]::GetForegroundWindow()) -ne $script:client.Id) {
  $shell=New-Object -ComObject WScript.Shell
  [void]$shell.AppActivate($script:client.Id)
 }
 $r=$dialogs[0].rect
 Click-Point @([int]($r.left+$r.width*0.32),[int]($r.top+$r.height*0.82)) $false $true
 Start-Sleep -Milliseconds 300
 $result=Doctor
 $result.operation='logout_confirmed'
 if ($result.status -eq 'trade_unavailable') {$result.status='auth_required';$result.auth_reason='explicit_logout_confirmed'}
 return $result
}

try {
 switch($request.action) {
  'doctor' {
   Discover
   if ($request.focus -and $script:client) {
    foreach($w in $script:windows){if($w.title -eq '东方财富终端' -and [EMDesktop]::IsIconic([IntPtr]$w.handle)){[void][EMDesktop]::ShowWindow([IntPtr]$w.handle,9)}}
    $shell=New-Object -ComObject WScript.Shell
    [void]$shell.AppActivate($script:client.Id)
   }
   if ($request.open_trade -and $script:client -and $script:trade -eq [IntPtr]::Zero) {
    $hidden=@($script:windows | Where-Object {$_.title -eq '东方财富证券' -and $_.class -eq '#32770' -and [EMDesktop]::Class([EMDesktop]::GetDlgItem([IntPtr]$_.handle,1002)) -eq 'ContainerWnd_DC'})
    if ($hidden.Count -eq 1) {[void][EMDesktop]::ShowWindow([IntPtr]$hidden[0].handle,9)}
    elseif ($request.restore_layout) {
     $main=@($script:windows | Where-Object {$_.visible -and $_.title -eq '东方财富终端'})
     if($main.Count -ne 1){throw 'layout_changed|Main client window is ambiguous'}
     $layout=$request.restore_layout
     if($main[0].rect.width -ne $layout.width -or $main[0].rect.height -ne $layout.height -or $main[0].dpi -ne $layout.dpi -or -not $layout.screenshot_sha256){throw 'layout_changed|Main client layout differs from reviewed screenshot'}
     $point=@([int]($main[0].rect.left+$main[0].rect.width*$layout.anchor[0]),[int]($main[0].rect.top+$main[0].rect.height*$layout.anchor[1]))
     Click-Point $point
    }
    else {throw 'screenshot_review_required|Review doctor screenshot showing the folded interface and pass --reviewed-screenshot'}
    Start-Sleep -Milliseconds 200
   }
   $result=Doctor
  }
  'start' {$result=Start-Client}
  'dismiss_ipo' {$result=Dismiss-Ipo}
  'snapshot' {$result=Capture-Export}
  'form_fill' {$result=Form-Check}
  'form_clear' {$result=Form-Check $true}
  'funds' {Require-Trade;Click-Target 'funds';$result=@{status='ok';operation='funds_panel_toggled';machine_extraction='not_verified'}}
  'logout' {Require-Trade;Click-Target 'logout';Start-Sleep -Milliseconds 300;$result=Doctor;$result.operation='logout_requested'}
  'logout_confirm' {$result=Confirm-Logout}
  'restart' {$result=Restart-Client}
  default {throw 'unsupported_action|No live submission or broker cancellation action is available'}
 }
 $result | ConvertTo-Json -Compress -Depth 10
} catch {
 $parts=$_.Exception.Message -split '\|',2
 $code=if($parts.Count -eq 2){$parts[0]}else{'driver_error'}
 $message=if($parts.Count -eq 2){$parts[1]}else{$_.Exception.Message}
  @{status='error';code=$code;message=$message;draft_written=$script:draftWritten} | ConvertTo-Json -Compress -Depth 4
}
