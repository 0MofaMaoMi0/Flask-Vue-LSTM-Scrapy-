param([switch]$Rollback)
$ErrorActionPreference='Stop'
$target='E:\Study tool\Vmware\VMware-data\Ubuntu 64 位'
$projectRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
$record=Join-Path $projectRoot '项目工作日志/06-开发与验证/M9-运维与安全收尾-20261114'
$resultPath=Join-Path $record 'ACL管理员执行结果.json'
function Inventory {
    $queue=[Collections.Generic.Queue[string]]::new();$queue.Enqueue($target)
    $items=[Collections.Generic.List[object]]::new()
    while($queue.Count){
        $p=$queue.Dequeue();$i=Get-Item -LiteralPath $p -Force
        if($i.FullName -cne $target -and !$i.FullName.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'OUTSIDE_TARGET'}
        if($i.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'REPARSE_POINT_REJECTED'}
        $items.Add($i)
        if($i.PSIsContainer){foreach($child in Get-ChildItem -LiteralPath $p -Force){$queue.Enqueue($child.FullName)}}
    }
    return $items.ToArray()
}
try {
    if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'ADMINISTRATOR_REQUIRED'}
    if((Get-Item -LiteralPath $target).FullName -cne $target){throw 'TARGET_MISMATCH'}
    $vmx=Join-Path $target 'Ubuntu 64 位.vmx'
    if(!(Select-String -LiteralPath $vmx -Pattern '^uuid.bios = "56 4d 61 3f 36 74 96 7a-c8 ea a7 86 bc c0 22 65"$' -Quiet)){throw 'VM_IDENTITY_MISMATCH'}
    $original=Get-Content -LiteralPath (Join-Path $record '修改前/VM目录ACL.json') -Raw -Encoding utf8|ConvertFrom-Json
    if($original.root -cne $target){throw 'BACKUP_SCOPE_MISMATCH'}
    $items=@(Inventory);$parent=Split-Path -LiteralPath $target;$parentBefore=(Get-Acl -LiteralPath $parent).Sddl
    $everyone=[Security.Principal.SecurityIdentifier]::new('S-1-1-0')
    $snapshot=@($items|ForEach-Object{[pscustomobject]@{path=$_.FullName;sddl=(Get-Acl -LiteralPath $_.FullName).Sddl}})
    $snapshot|ConvertTo-Json -Depth 4|Set-Content -LiteralPath (Join-Path $record ('ACL管理员执行前-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')+'.json')) -Encoding utf8
    if($Rollback){
        foreach($entry in $original.items){
            if($entry.path -cne $target -and !$entry.path.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'BACKUP_OUTSIDE_TARGET'}
            if(!(Test-Path -LiteralPath $entry.path)){continue}
            $acl=Get-Acl -LiteralPath $entry.path
            $acl.SetSecurityDescriptorSddlForm($entry.sddl,[Security.AccessControl.AccessControlSections]::Access)
            Set-Acl -LiteralPath $entry.path -AclObject $acl
        }
    } else {
        # Root inheritance must be stopped so Everyone cannot return from its parent.
        # Materialize inherited ACEs first, then remove Allow rules from the fresh descriptor.
        $acl=Get-Acl -LiteralPath $target;$acl.SetAccessRuleProtection($true,$true)
        $fresh=[Security.AccessControl.DirectorySecurity]::new()
        $fresh.SetSecurityDescriptorSddlForm($acl.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access))
        foreach($r in @($fresh.Access)){if($r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $everyone -and $r.AccessControlType -eq 'Allow'){$fresh.RemoveAccessRuleAll($r)}}
        if(@($fresh.Access|Where-Object{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $everyone -and $_.AccessControlType -eq 'Allow'}).Count){throw 'IN_MEMORY_ACL_CHECK_FAILED'}
        Set-Acl -LiteralPath $target -AclObject $fresh
        foreach($item in @(Inventory)){
            $acl=Get-Acl -LiteralPath $item.FullName;$changed=$false
            foreach($r in @($acl.Access)){if(!$r.IsInherited -and $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]) -eq $everyone -and $r.AccessControlType -eq 'Allow'){$acl.RemoveAccessRuleAll($r);$changed=$true}}
            if($changed){Set-Acl -LiteralPath $item.FullName -AclObject $acl}
        }
    }
    $after=@(Inventory);$bad=0;$permissionsMissing=0;$ownersChanged=0
    $userSid=[Security.Principal.NTAccount]::new('MEIFANNAO','20876').Translate([Security.Principal.SecurityIdentifier]).Value
    foreach($item in $after){
        $a=Get-Acl -LiteralPath $item.FullName
        if(@($a.Access|Where-Object{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq 'S-1-1-0' -and $_.AccessControlType -eq 'Allow'}).Count){$bad++}
        foreach($required in @(@($userSid,1245631),@('S-1-5-18',2032127),@('S-1-5-32-544',2032127))){
            if(!@($a.Access|Where-Object{$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $required[0] -and $_.AccessControlType -eq 'Allow' -and (([int]$_.FileSystemRights -band $required[1]) -eq $required[1])}).Count){$permissionsMissing++}
        }
        $old=@($original.items|Where-Object path -eq $item.FullName)
        if($old.Count -eq 1 -and $old[0].owner -ne $a.Owner){$ownersChanged++}
    }
    $sameParent=((Get-Acl -LiteralPath $parent).Sddl -eq $parentBefore)
    if(!$sameParent -or (!$Rollback -and ($bad -or $permissionsMissing -or $ownersChanged))){throw 'POSTCHECK_FAILED'}
    [pscustomobject]@{success=$true;rollback=[bool]$Rollback;objects=$after.Count;everyone_allow_objects=$bad;missing_required_permissions=$permissionsMissing;owners_changed=$ownersChanged;parent_unchanged=$sameParent}|ConvertTo-Json|Set-Content -LiteralPath $resultPath -Encoding utf8
} catch {
    [pscustomobject]@{success=$false;rollback=[bool]$Rollback;error_code=$_.Exception.Message}|ConvertTo-Json|Set-Content -LiteralPath $resultPath -Encoding utf8
    exit 1
}
