param([Parameter(Mandatory=$true)][string]$RequestPath)
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new()
try {
 $request=[IO.File]::ReadAllText($RequestPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
 if ($request.host_action -eq 'secure') {
  $folder=[IO.DirectoryInfo]::new($request.path)
  if (-not $folder.Exists -or $folder.FullName.StartsWith('\\')) {throw 'local_path_required|Runtime storage must be a local Windows directory'}
  $acl=[Security.AccessControl.DirectorySecurity]::new()
  $acl.SetAccessRuleProtection($true,$false)
  $rights=[Security.AccessControl.FileSystemRights]::FullControl
  $inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
  $prop=[Security.AccessControl.PropagationFlags]::None
  foreach($sid in @([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.Principal.SecurityIdentifier]::new('S-1-5-18'))) {
   $rule=[Security.AccessControl.FileSystemAccessRule]::new($sid,$rights,$inherit,$prop,[Security.AccessControl.AccessControlType]::Allow)
   $acl.AddAccessRule($rule)
  }
  $folder.SetAccessControl($acl)
  $actual=$folder.GetAccessControl()
  $rules=@($actual.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
  $allowed=@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18')
  if(-not $actual.AreAccessRulesProtected -or $rules.Count -ne 2) {throw 'storage_permissions_failed|Private runtime ACL could not be verified'}
  foreach($rule in $rules){if($rule.IdentityReference.Value -notin $allowed -or $rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow -or $rule.FileSystemRights -ne $rights){throw 'storage_permissions_failed|Unexpected runtime access rule'}}
  @{status='ok';storage='private_ntfs_acl';acl_protected=$actual.AreAccessRulesProtected;principal_count=$rules.Count} | ConvertTo-Json -Compress
  exit 0
 }
 Add-Type -TypeDefinition @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class EMNativeHost {
 [DllImport("user32.dll",SetLastError=true)] public static extern IntPtr OpenInputDesktop(uint flags,bool inherit,uint access);
 [DllImport("user32.dll",SetLastError=true)] public static extern bool CloseDesktop(IntPtr h);
 [DllImport("user32.dll",CharSet=CharSet.Unicode,SetLastError=true)] public static extern bool GetUserObjectInformation(IntPtr h,int index,StringBuilder data,int size,out int needed);
 [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
 public static string InputDesktop() {
  IntPtr h=OpenInputDesktop(0,false,1);
  if(h==IntPtr.Zero) throw new Exception("desktop_unavailable|Unlock the Windows desktop before using desktop automation");
  try {var b=new StringBuilder(256);int size;if(!GetUserObjectInformation(h,2,b,512,out size))throw new Exception("desktop_unavailable|Cannot identify input desktop");return b.ToString();}
  finally {CloseDesktop(h);}
 }
}
'@
 $session=[Diagnostics.Process]::GetCurrentProcess().SessionId
 if (-not [Environment]::UserInteractive -or $session -eq 0) {throw 'interactive_session_required|Run from the signed-in Windows desktop, not a service or session zero'}
 $desktop=[EMNativeHost]::InputDesktop()
 if ($desktop -ne 'Default') {throw 'desktop_unavailable|Unlock the default Windows desktop and resolve secure-desktop prompts'}
 [void][EMNativeHost]::SetThreadDpiAwarenessContext([IntPtr](-4))
 switch($request.host_action) {
  'probe' {
   @{status='ok';transport='windows_local';session=$session;desktop=$desktop;echo=$request.echo;powershell=$PSVersionTable.PSVersion.ToString();architecture=$env:PROCESSOR_ARCHITECTURE} | ConvertTo-Json -Compress
  }
  'capture' {
   Add-Type -AssemblyName System.Windows.Forms
   Add-Type -AssemblyName System.Drawing
   $bounds=[Windows.Forms.Screen]::PrimaryScreen.Bounds
   $bitmap=[Drawing.Bitmap]::new($bounds.Width,$bounds.Height)
   $graphics=[Drawing.Graphics]::FromImage($bitmap)
   try {
    $graphics.CopyFromScreen($bounds.Location,[Drawing.Point]::Empty,$bounds.Size)
    $bitmap.Save($request.path,[Drawing.Imaging.ImageFormat]::Png)
   } finally {$graphics.Dispose();$bitmap.Dispose()}
   @{status='ok';width=$bounds.Width;height=$bounds.Height;session=$session;desktop=$desktop} | ConvertTo-Json -Compress
  }
  'driver' {
   $json=$request.driver_request | ConvertTo-Json -Depth 12 -Compress
   & ([scriptblock]::Create([IO.File]::ReadAllText($request.driver_path,[Text.Encoding]::UTF8))) -RequestJson $json
  }
  default {throw 'unsupported_action|Unknown Windows host action'}
 }
} catch {
 $parts=$_.Exception.Message -split '\|',2
 $code=if($parts.Count -eq 2){$parts[0]}else{'windows_host_error'}
 $message=if($parts.Count -eq 2){$parts[1]}else{'Windows host failed; check local storage permissions and the interactive desktop'}
 @{status='error';code=$code;message=$message} | ConvertTo-Json -Compress
 exit 2
}
