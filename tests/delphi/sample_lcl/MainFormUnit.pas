unit MainFormUnit;

{$mode objfpc}{$H+}

{
  Sample LCL (Lazarus) / VCL-compatible form for dolphin_desktop's
  Delphi backend tests. Same TComponent hierarchy that a real Delphi
  VCL app would expose - all standard controls with developer-set
  Names matching what the pytest suite expects.

  Compile with:
    lazbuild sample_lcl.lpi
  Produces sample_lcl.exe.
}

interface

uses
  Classes, SysUtils, Forms, Controls, StdCtrls, ExtCtrls, ComCtrls, Grids, Menus, Dialogs;

type
  TMainForm = class(TForm)
    LblStatus:      TStaticText;
    LblName:        TStaticText;
    EdtName:        TEdit;
    LblAge:         TStaticText;
    EdtAge:         TEdit;
    LblPassword:    TStaticText;
    EdtPassword:    TEdit;
    LblReadOnly:    TStaticText;
    EdtReadOnly:    TEdit;
    LblMemo:        TStaticText;
    MemoLog:        TMemo;
    LblGrid:        TStaticText;
    Grid:           TListView;
    ChkActive:      TCheckBox;
    ChkNews:        TCheckBox;
    RadStandard:    TRadioButton;
    RadPremium:     TRadioButton;
    CmbCountry:     TComboBox;
    LstHobbies:     TListBox;
    PageControl:    TPageControl;
    TabDetails:     TTabSheet;
    TabAdvanced:    TTabSheet;
    GrpBilling:     TGroupBox;
    Progress:       TProgressBar;
    BtnSave:        TButton;
    BtnClear:       TButton;
    BtnAppendLog:   TButton;
    BtnDisabled:    TButton;
    BtnAdvance:     TButton;
    MainMenu:       TMainMenu;
    MnuFile:        TMenuItem;
    MnuFileExit:    TMenuItem;
    MnuEdit:        TMenuItem;
    MnuEditCopy:    TMenuItem;
    procedure FormCreate(Sender: TObject);
    procedure BtnSaveClick(Sender: TObject);
    procedure BtnClearClick(Sender: TObject);
    procedure BtnAppendLogClick(Sender: TObject);
    procedure BtnAdvanceClick(Sender: TObject);
    procedure MnuFileExitClick(Sender: TObject);
  end;

var
  MainForm: TMainForm;

implementation

{$R *.lfm}

procedure TMainForm.FormCreate(Sender: TObject);
begin
  // Seed the combo/list/grid so tests can assert on known state.
  CmbCountry.Items.Add('Poland');
  CmbCountry.Items.Add('Germany');
  CmbCountry.Items.Add('France');
  CmbCountry.ItemIndex := 0;

  LstHobbies.Items.Add('Reading');
  LstHobbies.Items.Add('Cycling');
  LstHobbies.Items.Add('Cooking');

  Grid.ViewStyle := vsReport;
  Grid.Columns.Add.Caption := 'ID';
  Grid.Columns.Add.Caption := 'Name';
  Grid.Columns.Add.Caption := 'Score';
  Grid.Columns[0].Width := 60;
  Grid.Columns[1].Width := 140;
  Grid.Columns[2].Width := 80;
  with Grid.Items.Add do
  begin
    Caption := '1'; SubItems.Add('Alice'); SubItems.Add('92');
  end;
  with Grid.Items.Add do
  begin
    Caption := '2'; SubItems.Add('Bob'); SubItems.Add('85');
  end;
  with Grid.Items.Add do
  begin
    Caption := '3'; SubItems.Add('Carol'); SubItems.Add('78');
  end;

  EdtReadOnly.Text := 'read-only';
  EdtReadOnly.ReadOnly := True;
  EdtPassword.PasswordChar := '*';

  Progress.Min := 0;
  Progress.Max := 100;
  Progress.Position := 25;

  BtnDisabled.Enabled := False;

  LblStatus.Caption := 'Ready.';
end;

procedure TMainForm.BtnSaveClick(Sender: TObject);
var Tier: string;
begin
  if RadPremium.Checked then Tier := 'Premium' else Tier := 'Standard';
  LblStatus.Caption := Format(
    'Saved: name=%s age=%s active=%s tier=%s country=%s',
    [EdtName.Text, EdtAge.Text, BoolToStr(ChkActive.Checked, True),
     Tier, CmbCountry.Text]
  );
end;

procedure TMainForm.BtnClearClick(Sender: TObject);
begin
  EdtName.Text := '';
  EdtAge.Text := '';
  EdtPassword.Text := '';
  MemoLog.Lines.Clear;
  ChkActive.Checked := False;
  ChkNews.Checked := False;
  RadStandard.Checked := True;
  CmbCountry.ItemIndex := 0;
  LstHobbies.ItemIndex := -1;
  Progress.Position := 0;
  LblStatus.Caption := 'Cleared.';
end;

procedure TMainForm.BtnAppendLogClick(Sender: TObject);
begin
  MemoLog.Lines.Add(Format('Entry %d at tick', [MemoLog.Lines.Count + 1]));
  LblStatus.Caption := Format('Log has %d entries.', [MemoLog.Lines.Count]);
end;

procedure TMainForm.BtnAdvanceClick(Sender: TObject);
begin
  Progress.Position := Progress.Position + 10;
  if Progress.Position >= Progress.Max then
    Progress.Position := Progress.Max;
  LblStatus.Caption := Format('Progress: %d%%', [Progress.Position]);
end;

procedure TMainForm.MnuFileExitClick(Sender: TObject);
begin
  Close;
end;

end.
