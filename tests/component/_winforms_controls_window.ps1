# Controlled DESKTOP-145 WinForms AUT; also exercises DPI-aware owners.
param([switch]$DpiAware)

$ErrorActionPreference = "Stop"

if ($DpiAware) {
    Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class DolphinDpi {
    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
'@
    if (-not [DolphinDpi]::SetProcessDPIAware()) {
        throw "Could not enable DPI awareness for the controlled WinForms AUT"
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

[System.Windows.Forms.Application]::EnableVisualStyles()
[System.Windows.Forms.Application]::SetCompatibleTextRenderingDefault($false)

$form = [System.Windows.Forms.Form]::new()
$form.Text = "Dolphin Win32 Legacy Fixture"
$form.Name = "DolphinWin32LegacyFixture"
$form.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$form.ClientSize = [System.Drawing.Size]::new(640, 420)

$menu = [System.Windows.Forms.MainMenu]::new()
$fileMenu = [System.Windows.Forms.MenuItem]::new("File")
$showDialog = [System.Windows.Forms.MenuItem]::new("Show dialog")
[void]$fileMenu.MenuItems.Add($showDialog)
[void]$menu.MenuItems.Add($fileMenu)
$form.Menu = $menu

$editLabel = [System.Windows.Forms.Label]::new()
$editLabel.Text = "Account name"
$editLabel.Location = [System.Drawing.Point]::new(16, 20)
$editLabel.AutoSize = $true

$edit = [System.Windows.Forms.TextBox]::new()
$edit.Name = "Win32AccountEdit"
$edit.Location = [System.Drawing.Point]::new(140, 16)
$edit.Width = 240

$comboLabel = [System.Windows.Forms.Label]::new()
$comboLabel.Text = "Plan"
$comboLabel.Location = [System.Drawing.Point]::new(16, 58)
$comboLabel.AutoSize = $true

$combo = [System.Windows.Forms.ComboBox]::new()
$combo.Name = "Win32PlanCombo"
$combo.Location = [System.Drawing.Point]::new(140, 54)
$combo.Width = 180
$combo.DropDownStyle = [System.Windows.Forms.ComboBoxStyle]::DropDownList
[void]$combo.Items.Add("Standard")
[void]$combo.Items.Add("Premium")
$combo.SelectedIndex = 0

$save = [System.Windows.Forms.Button]::new()
$save.Name = "Win32SaveButton"
$save.Text = "Save"
$save.Location = [System.Drawing.Point]::new(400, 15)
$save.AutoSize = $true

$treeLabel = [System.Windows.Forms.Label]::new()
$treeLabel.Text = "Navigation"
$treeLabel.Location = [System.Drawing.Point]::new(16, 105)
$treeLabel.AutoSize = $true

$tree = [System.Windows.Forms.TreeView]::new()
$tree.Name = "Win32NavigationTree"
$tree.Location = [System.Drawing.Point]::new(16, 130)
$tree.Size = [System.Drawing.Size]::new(260, 210)
$root = $tree.Nodes.Add("Root")
[void]$root.Nodes.Add("Child")

$status = [System.Windows.Forms.Label]::new()
$status.Name = "Win32Status"
$status.Text = "ready"
$status.Location = [System.Drawing.Point]::new(310, 135)
$status.AutoSize = $true

$form.Controls.AddRange(@(
    $editLabel,
    $edit,
    $comboLabel,
    $combo,
    $save,
    $treeLabel,
    $tree,
    $status
))

$edit.Add_TextChanged({
    $status.Text = "input=$($edit.Text)"
})
$combo.Add_SelectedIndexChanged({
    if ($null -ne $combo.SelectedItem) {
        $status.Text = "combo=$($combo.SelectedItem)"
    }
})
$save.Add_Click({
    $status.Text = "saved=1"
})
$tree.Add_AfterSelect({
    param($sender, $eventArgs)
    $status.Text = "tree=$($eventArgs.Node.Text)"
})
$showDialog.Add_Click({
    [void][System.Windows.Forms.MessageBox]::Show(
        $form,
        "Dialog ready",
        "Dolphin Win32 dialog",
        [System.Windows.Forms.MessageBoxButtons]::OK
    )
    $status.Text = "dialog_closed=1"
})

[System.Windows.Forms.Application]::Run($form)

