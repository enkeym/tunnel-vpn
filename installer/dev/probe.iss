; Пробный мастер: размер окна и вид тёмного стиля на этой Windows.
; Без прав и без файлов — ставить нечего, только листать страницы.
; Собирает и снимает installer/dev/snap.ps1, в сборку не входит.

[Setup]
AppName=TunnelVPN
AppVersion=0.3.7-beta
PrivilegesRequired=lowest
DefaultDirName={autopf}\TunnelVPN
DisableDirPage=no
Uninstallable=no
OutputDir=out
OutputBaseFilename=probe
WizardStyle=modern dark includetitlebar hidebevels
WizardBackColor=#101012
DisableWelcomePage=no
DisableReadyPage=yes

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[Messages]
WelcomeLabel2=Несколько туннелей WireGuard и AmneziaWG одновременно: каждый сайт идёт через свой выход, остальное — напрямую.%n%nБета-версия.

[Code]
procedure InitializeWizard;
var
  Page: TInputOptionWizardPage;
begin
  Page := CreateInputOptionPage(wpWelcome, 'TunnelVPN уже установлен',
    'Установлено: 0.3.5 · В этом файле: 0.3.7', 'Конфиги, правила и настройки сохранятся.', True, False);
  Page.Add('Обновить до 0.3.7');
  Page.Add('Удалить');
  Page.SelectedValueIndex := 0;
end;
