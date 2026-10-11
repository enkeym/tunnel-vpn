; Установщик TunnelVPN. Собирается из installer\build.ps1, руками звать не нужно.
;
; Что делает сверх обычного копирования файлов:
;   * заводит C:\ProgramData\TunnelVPN с раздельными правами на conf\ и state\
;   * ставит и запускает службу TunnelVPN
;   * при удалении снимает службу, но оставляет конфиги
;   * поверх установленной версии спрашивает: обновить или удалить
;   * тихое обновление возвращает туннель и трей, как они были
;   * переносит установку под прежним именем DualVPN (MigrateLegacy в [Code])
;   * ставит WebView2 Runtime, если его нет (EnsureWebView2 в [Code])
;
; ВАЖНО: файл в UTF-8 С BOM. Без BOM Inno Setup читает его как ANSI, и вся
; кириллица в подписях кнопок и сообщениях превращается в мусор. Та же беда,
; что и у build.ps1. Не пересохраняй без BOM.

#ifndef MyVersion
  #define MyVersion "0.0.0"
#endif

; Тёмный вид мастера: WizardStyle dark — с Inno 6.6, WizardBackColor — с 6.7.0.
; В CI закреплена 6.7.1 (.github/workflows/build.yml). На старой ISCC сборка
; дала бы другой вид или ошибку в директиве — лучше упасть здесь, понятно.
#if Ver < EncodeVer(6, 7, 1)
  #error Нужен Inno Setup 6.7.1 или новее
#endif

; Тестовая сборка (build.ps1 -TestHooks): /testhook=… на командной строке
; ломает первую попытку шага — сценарии сбоев проверки в Sandbox (TestHook в
; [Code]). Отдельное имя файла, метка в ресурсе версии и в заголовке окна;
; build.ps1 и CI не пускают такую сборку в релиз. Подписанной ей не бывать.
#if defined(TESTHOOKS) && defined(Sign)
  #error Тестовая сборка (TESTHOOKS) не подписывается
#endif

; Версия без хвоста «-beta»: её показывает мастер, и только числа принимает
; VersionInfoVersion.
#if Pos("-", MyVersion) > 0
  #define MyPlainVersion Copy(MyVersion, 1, Pos("-", MyVersion) - 1)
#else
  #define MyPlainVersion MyVersion
#endif

#define MyName "TunnelVPN"
#define MyExe "TunnelVPN-Tray.exe"
#define MyCli "tunnelvpn.exe"
; Издатель — тот же, что CompanyName в ресурсе версии exe (tunnelvpn.spec).
#define MyPublisher "Enkeym"
#define MyRepo "https://github.com/sotakeiteasy/dual-vpn"
; Палитра окна приложения (lib/tunnelvpn/ui/app.css). --card там — 5 % белого
; поверх --bg, здесь посчитан.
#define ColorBg "#101012"
#define ColorDim "#9a9aa6"
#define ColorCard "#1c1c1e"
#define ColorOff "#6b6b76"
#define ColorOk "#3df5a7"
#define ColorWarn "#ffd23f"
#define ColorErr "#ff5c7a"
; Прежнее имя проекта: с него переезжают уже установленные копии.
#define LegacyName "DualVPN"
#define LegacyExe "DualVPN-Tray.exe"
#define LegacyCli "dualvpn.exe"

[Setup]
; Тот же GUID — в UninstKey в [Code]: по нему ищется установленная версия.
AppId={{8F3A5C21-4E7B-4D96-9A1F-2C6B8D4E7A31}
AppName={#MyName}
AppVersion={#MyVersion}
AppPublisher={#MyPublisher}
; «Приложения и возможности»: ссылки «Сайт», «Справка» и «Обновления».
AppPublisherURL={#MyRepo}
AppSupportURL={#MyRepo}/issues
AppUpdatesURL={#MyRepo}/releases
; Без версии в имени: она в своём поле.
UninstallDisplayName={#MyName}
; Ресурс версии setup.exe: его описание показывает окно UAC.
VersionInfoVersion={#MyPlainVersion}
VersionInfoTextVersion={#MyVersion}
VersionInfoProductTextVersion={#MyVersion}
VersionInfoCompany={#MyPublisher}
#ifdef TESTHOOKS
VersionInfoDescription=Установка {#MyName} (TESTHOOKS)
#else
VersionInfoDescription=Установка {#MyName}
#endif
VersionInfoProductName={#MyName}
; Папка прошлой установки TunnelVPN — из реестра, см. DefaultAppDir в [Code].
DefaultDirName={code:DefaultAppDir}
DefaultGroupName={#MyName}
; AppId общий с DualVPN, и Inno взял бы её папку и группу ярлыков из реестра:
; программа осталась бы в Program Files\DualVPN под новым именем.
; Свою прошлую папку берёт DefaultAppDir, отличая её от папки DualVPN.
UsePreviousAppDir=no
UsePreviousGroup=no
UninstallDisplayIcon={app}\{#MyExe}
OutputDir=Output
#ifdef TESTHOOKS
OutputBaseFilename={#MyName}-{#MyVersion}-testhooks-setup
#else
OutputBaseFilename={#MyName}-{#MyVersion}-setup
#endif
SetupIconFile=gen\TunnelVPN.ico
Compression=lzma2/max
SolidCompression=yes
; Только тёмный, как окно приложения, и с тёмным заголовком окна; без
; линий-разделителей. Цвет фона — --bg окна, для мастера и деинсталлятора.
WizardStyle=modern dark includetitlebar hidebevels
WizardBackColor={#ColorBg}
; Все масштабы из installer\gen (make_icon.py: WIZARD, WIZARD_SMALL): Inno
; берёт ближайший к DPI экрана. Список — тот же, что проверяет build.ps1.
WizardImageFile=gen\wizard-202x386.png,gen\wizard-269x515.png,gen\wizard-336x643.png,gen\wizard-403x772.png,gen\wizard-430x824.png,gen\wizard-534x1022.png
WizardSmallImageFile=gen\wizard-small-58.png,gen\wizard-small-77.png,gen\wizard-small-97.png,gen\wizard-small-116.png,gen\wizard-small-124.png,gen\wizard-small-159.png
; Новая установка: приветствие → папка → параметры (своя страница в [Code])
; → установка. Поверх установленной приветствие и папку пропускаем, см.
; ShouldSkipPage. «Готово к установке» нет: «Установить» — на параметрах.
DisableWelcomePage=no
DisableProgramGroupPage=yes
DisableReadyPage=yes
; Служба ставится в систему и правит маршруты — без администратора никак.
PrivilegesRequired=admin
; x64os, а не x64compatible: на ARM64 эмуляция x64 запустила бы программу, но
; wintun.dll под amd64 не загрузится в ядро ARM64 — туннель не поднять.
ArchitecturesInstallIn64BitMode=x64os
ArchitecturesAllowed=x64os
; Windows 10 1809 — минимум, где WebView2 и wintun ведут себя предсказуемо.
MinVersion=10.0.17763
; Журнал установки и удаления в %TEMP% («Setup Log …», «Uninstall Log …»):
; без него сбой на чужой машине не разобрать. Путь — в сообщении об ошибке.
SetupLogging=yes
UninstallLogging=yes
#ifdef Sign
; Подпись setup и деинсталлятора: команду tvpn задаёт build.ps1 (/Stvpn=…),
; только когда есть сертификат (TVPN_SIGN_SHA1). Без /DSign — без подписи.
SignTool=tvpn
#endif

[Languages]
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

; Стандартные тексты Inno, которые видны в наших сценариях: тем же языком,
; что окно приложения, на «вы». Остальные — из Russian.isl.
[Messages]
#ifdef TESTHOOKS
SetupWindowTitle=Установка — %1 (тестовая сборка)
#else
SetupWindowTitle=Установка — %1
#endif
UninstallAppTitle=Удаление
UninstallAppFullTitle=Удаление — %1
; Проверки до первой страницы: ArchitecturesAllowed и MinVersion в [Setup].
OnlyOnTheseArchitectures=TunnelVPN работает только на 64-битной Windows для процессоров Intel и AMD. На Windows для ARM он пока не работает: драйвер сетевого адаптера нельзя запустить через эмуляцию — туннель не поднимется.
WinVersionTooLowError=Нужна Windows 10 версии 1809 или новее.%n%nНа старых версиях не работают компоненты, без которых TunnelVPN не поднимет туннель.
ExitSetupTitle=Прервать установку
ExitSetupMessage=Установка не закончена. Если выйти сейчас, TunnelVPN не будет установлен.%n%nВыйти?
SetupAborted=Установка не закончена.%n%nУстраните причину и запустите установку снова.
ButtonBack=&Назад
ButtonNext=&Далее
ButtonFinish=&Готово
ButtonWizardBrowse=&Обзор…
WelcomeLabel1=[name] {#MyPlainVersion}
#if Pos("beta", MyVersion) > 0
WelcomeLabel2=Несколько туннелей WireGuard и AmneziaWG одновременно: каждый сайт идёт через свой выход, остальное — напрямую.%n%nБета-версия.
#else
WelcomeLabel2=Несколько туннелей WireGuard и AmneziaWG одновременно: каждый сайт идёт через свой выход, остальное — напрямую.
#endif
WizardSelectDir=Папка программы
SelectDirDesc=Куда поставить [name]
SelectDirLabel3=[name] встанет в эту папку.
SelectDirBrowseLabel=Конфиги и ключи хранятся отдельно — в C:\ProgramData\TunnelVPN.
DiskSpaceMBLabel=Нужно [mb] МБ на диске.
DiskSpaceGBLabel=Нужно [gb] ГБ на диске.
WizardPreparing=Подготовка
PreparingDesc=Останавливаю прежнюю версию, если она работает
CannotContinue=Установка остановлена. Нажмите «Отмена», чтобы выйти.
WizardInstalling=Установка
InstallingLabel=Ставлю [name] {#MyPlainVersion}
FinishedHeadingLabel=[name] установлен
FinishedLabelNoIcons=Откройте [name] и добавьте конфиг .conf — его выдаёт администратор сети или AmneziaVPN.
FinishedLabel=Откройте [name] и добавьте конфиг .conf — его выдаёт администратор сети или AmneziaVPN.
ClickFinish=
FinishedRestartLabel=Чтобы закончить установку, нужно перезагрузить компьютер. Перезагрузить сейчас?
FinishedRestartMessage=Чтобы закончить установку, нужно перезагрузить компьютер.%n%nПерезагрузить сейчас?
YesRadio=&Перезагрузить сейчас
NoRadio=П&озже
; Этапы установки в строке над прогрессом. Свои этапы — StatusMsg в [Run] и
; SetStatus в [Code].
StatusClosingApplications=Закрываю приложения…
StatusCreateDirs=Создаю папки…
StatusExtractFiles=Копирую файлы…
StatusCreateIcons=Создаю ярлыки…
StatusCreateRegistryEntries=Записываю параметры…
StatusSavingUninstall=Запоминаю, что удалять потом…
StatusRunProgram=Заканчиваю установку…
StatusRollback=Отменяю изменения…
; Удаление. Финальная фраза одна на оба ответа про конфиги (CurUninstallStepChanged).
ConfirmUninstall=Удалить %1?%n%nVPN отключится, служба и автозапуск будут удалены.
WizardUninstalling=Удаление
UninstallStatusLabel=Убираю %1 с компьютера
StatusUninstalling=Отключаю VPN…
UninstalledAll=%1 удалён. Если конфиги оставлены, они в C:\ProgramData\TunnelVPN\conf.
UninstalledMost=%1 удалён, но часть файлов удалить не получилось — удалите их вручную. Если конфиги оставлены, они в C:\ProgramData\TunnelVPN\conf.
UninstalledAndNeedsRestart=Чтобы закончить удаление %1, нужно перезагрузить компьютер.%n%nПерезагрузить сейчас?

; Описание задач здесь — для журнала и /TASKS. Пользователь видит их на
; странице «Параметры» с пояснениями (TasksPage в [Code]): стандартную
; страницу задач Inno пропускаем.
[Tasks]
Name: "autorun"; Description: "Запускать значок в трее при входе в Windows"
Name: "desktopicon"; Description: "Значок на рабочем столе"; Flags: unchecked

[Files]
; uninsrestartdelete: файл, который при удалении занят (служба журнала событий
; держит servicemanager.pyd, если хоть раз показывала записи службы), удаляется
; при перезагрузке, а не остаётся в Program Files навсегда.
Source: "..\dist\TunnelVPN\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs uninsrestartdelete
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
; Данные переживают обновление и удаление: uninsneveruninstall на conf\.
Name: "{commonappdata}\{#MyName}"
Name: "{commonappdata}\{#MyName}\conf"; Flags: uninsneveruninstall
Name: "{commonappdata}\{#MyName}\state"; Flags: uninsneveruninstall
Name: "{commonappdata}\{#MyName}\state\logs"; Flags: uninsneveruninstall
Name: "{commonappdata}\{#MyName}\state\run"; Flags: uninsneveruninstall

[Icons]
Name: "{group}\{#MyName}"; Filename: "{app}\{#MyExe}"
Name: "{group}\Удалить {#MyName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyName}"; Filename: "{app}\{#MyExe}"; Tasks: desktopicon
; Автозапуск трея — задачей планировщика (см. RegisterTrayTask в [Code]).

[InstallDelete]
; Ярлык автозапуска из прошлых версий: трей теперь требует администратора,
; и из «Автозагрузки» Windows его просто не запустит.
Type: files; Name: "{userstartup}\{#MyName}.lnk"
Type: files; Name: "{userstartup}\{#LegacyName}.lnk"
; Образцы конфигов, которые клали версии до 0.2.9: их больше не поставляем.
Type: filesandordirs; Name: "{app}\examples"

[Run]
; --- Права на данные.
; conf\ содержит приватные ключи: доступ только SYSTEM и администраторам.
; Обычный пользователь работает с конфигами через службу (см. ipc.py), а не
; напрямую по файловой системе.
Filename: "{sys}\icacls.exe"; \
  Parameters: """{commonappdata}\{#MyName}\conf"" /inheritance:r \
    /grant:r ""*S-1-5-18"":(OI)(CI)F /grant:r ""*S-1-5-32-544"":(OI)(CI)F"; \
  Flags: runhidden waituntilterminated; StatusMsg: "Настраиваю права на папку с ключами…"
; state\ читают трей и окно от обычного пользователя: там status.json и логи.
Filename: "{sys}\icacls.exe"; \
  Parameters: """{commonappdata}\{#MyName}\state"" /inheritance:r \
    /grant:r ""*S-1-5-18"":(OI)(CI)F /grant:r ""*S-1-5-32-544"":(OI)(CI)F \
    /grant:r ""*S-1-5-11"":(OI)(CI)RX"; \
  Flags: runhidden waituntilterminated
; state\run\ — собранные конфиги sing-box с теми же ключами, что в conf\:
; закрыт, как conf\. После state\: своё /inheritance:r не даёт наследовать RX.
; Сведения для окна служба отдаёт через канал (howto).
Filename: "{sys}\icacls.exe"; \
  Parameters: """{commonappdata}\{#MyName}\state\run"" /inheritance:r \
    /grant:r ""*S-1-5-18"":(OI)(CI)F /grant:r ""*S-1-5-32-544"":(OI)(CI)F"; \
  Flags: runhidden waituntilterminated

; --- Служба ставится в [Code] (InstallService, из CurStepChanged): здесь коды
; выхода не проверяются, и упавший «service install» давал «успешную»
; установку без службы.

; runascurrentuser: трей работает от администратора, а установщик уже с правами.
; По умолчанию postinstall запускает от исходного пользователя без прав, и
; трей спросил бы UAC ещё раз (см. tray.run).
Filename: "{app}\{#MyExe}"; Description: "Открыть {#MyName}"; \
  Flags: nowait postinstall skipifsilent runascurrentuser

[UninstallRun]
; Трей — отдельный процесс со своим списком открытых файлов в _internal\
; (общих с tunnelvpn.exe). Пока он жив, деинсталлятор не может удалить папку и
; падает на «файл занят» — снятие службы ниже этого не решает вовсе, служба
; и трей друг с другом никак не связаны. Убиваем первым делом, до всего.
; /T — вместе с окном, которое трей держит прогретым (tunnelvpn.exe window).
; Те же шаги в том же порядке, но с этапами в строке над прогрессом, до этого
; делает StopForUninstall в [Code]: здесь они проходят вхолостую. BeforeInstall
; тут не поможет — у [UninstallRun] его зовёт установка, а не удаление.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#MyExe} /F /T"; \
  Flags: runhidden waituntilterminated; RunOnceId: "KillTray"
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /TN ""{#MyName} Tray"" /F"; \
  Flags: runhidden waituntilterminated; RunOnceId: "DelTrayTask"
; Порядок важен: сначала опустить туннель, потом снимать службу. Иначе
; маршруты и правило брандмауэра останутся висеть, и сеть будет смотреть
; в удалённый адаптер.
Filename: "{app}\{#MyCli}"; Parameters: "stop"; \
  Flags: runhidden waituntilterminated; RunOnceId: "StopTunnel"
; net stop, а не «tunnelvpn service stop»: тот только посылает команду и сразу
; выходит, и файлы ещё заняты процессом службы, когда их начинают удалять.
Filename: "{sys}\net.exe"; Parameters: "stop {#MyName}"; \
  Flags: runhidden waituntilterminated; RunOnceId: "StopService"
; Окно, пережившее трей, держит tunnelvpn.exe и _internal\. Служба уже
; остановлена, так что под этим именем остались только окна.
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#MyCli} /F /T"; \
  Flags: runhidden waituntilterminated; RunOnceId: "KillWindow"
Filename: "{app}\{#MyCli}"; Parameters: "service remove"; \
  Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
; Логи и рабочее состояние уносим, конфиги — нет: их клали руками, и
; восстанавливать ключи после переустановки человек не обязан.
Type: filesandordirs; Name: "{commonappdata}\{#MyName}\state"
; Всё, что появилось в папке программы уже после установки, — её же мусор.
Type: filesandordirs; Name: "{app}"

[Code]
const
  UninstKey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{8F3A5C21-4E7B-4D96-9A1F-2C6B8D4E7A31}_is1';
  WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703';
  // Туннель после обновления (TunnelRestore): не был включён, включён снова,
  // поднимает служба сама (автоподключение), сам не поднялся.
  TunnelNotUp = 0;
  TunnelBack = 1;
  TunnelAuto = 2;
  TunnelLost = 3;

var
  // Меню «уже установлено»; nil — ставим впервые.
  ActionPage: TWizardPage;
  // Пункт установки (обновить, переустановить или откатить) и «Удалить». При
  // переезде с DualVPN пункт один: RemoveRadio — nil.
  InstallRadio, RemoveRadio: TNewRadioButton;
  // Подпись кнопки «Далее» для пункта установки: «Обновить» и т. п.
  InstallCaption: String;
  ParamsLink: TNewLinkLabel;
  // «Изменить параметры» в меню: показать «Параметры» и при обновлении.
  ChangeTasks: Boolean;
  OldUninstaller: String;
  // Удаление из меню прошло или установка не началась — закрываем мастер без
  // «Выйти из установки?».
  Leaving: Boolean;
  // Почему PrepareToInstall не дал ставить: CurPageChanged перестраивает
  // по ним страницу «Подготовка». '' — всё в порядке.
  PrepareError, PrepareTitle, PrepareDesc: String;
  PrepareRestart: Boolean;
  // «Готово», перестроенная в страницу ошибки службы (ShowServiceError).
  FinishedHeading: String;
  ErrIcon, ErrReason, ErrLog, ErrFoot: TNewStaticText;
  RetryButton, LogButton, CopyButton, ReportButton: TNewButton;
  // Что было до обновления: RestoreAfterUpdate возвращает так же.
  TunnelWasUp, TrayWasRunning: Boolean;
  // Чем кончилось возвращение туннеля: Tunnel* выше. TunnelLost — код выхода 11.
  TunnelRestore: Integer;
  // «Готово» поверх установленной (CreateActionPage): заголовок и текст.
  UpdatedHeading, FinishedText: String;
  // ● перед «VPN снова включён» и плашка ⚠, если туннель сам не поднялся.
  OkDot: TNewStaticText;
  WarnPlate: TPanel;
  // Пояснение под прогрессом, пока ставится WebView2.
  WebView2Note: TNewStaticText;
  // WebView2 так и не поставился — код выхода 12.
  WebView2Missing: Boolean;
  // Что не вышло со службой; '' — работает. Не пусто — код выхода 10.
  ServiceError: String;
  // «Параметры»: задачи с пояснениями вместо стандартной страницы Inno.
  TasksPage: TWizardPage;
  AutorunBox, DesktopBox: TNewCheckBox;
  FinishedLink: TNewLinkLabel;
  // Удаление: «Удалить навсегда» в вопросе про конфиги.
  PurgeData: Boolean;
#ifdef TESTHOOKS
  // Хуки /testhook=…, ещё не сработавшие: «,service,webview2,».
  TestHooks: String;
  TestHooksRead: Boolean;
  // /testhook=webview2: WebView2Installed отвечает «нет» до конца первой попытки.
  WebView2Hook: Boolean;
#endif

#ifdef TESTHOOKS
// Тестовая сборка: /testhook=service,tunnel,webview2 (любые через запятую)
// ломает первую попытку шага — запуск службы, возвращение туннеля после
// обновления, установку WebView2. Повтор идёт по-настоящему: видны и
// страница ошибки, и «Повторить». True — хук Name сработал, второй раз — нет.
function TestHook(const Name: String): Boolean;
begin
  if not TestHooksRead then
  begin
    TestHooks := ',' + Lowercase(ExpandConstant('{param:testhook|}')) + ',';
    TestHooksRead := True;
  end;
  Result := Pos(',' + Name + ',', TestHooks) > 0;
  if Result then
  begin
    StringChangeEx(TestHooks, ',' + Name + ',', ',', True);
    Log('Тестовый хук: ' + Name);
  end;
end;
#endif

// Этап в строке над прогрессом — и установки, и удаления. Он же в журнал:
// при тихом удалении формы прогресса может не быть.
procedure SetStatus(const S: String);
begin
  Log('Этап: ' + S);
  if not IsUninstaller then
    WizardForm.StatusLabel.Caption := S
  else if UninstallProgressForm <> nil then
    UninstallProgressForm.StatusLabel.Caption := S;
end;

// Текст с переносом по ширине Width.
function NewText(Parent: TWinControl; Left, Top, Width: Integer; const Text: String): TNewStaticText;
begin
  Result := TNewStaticText.Create(WizardForm);
  Result.Parent := Parent;
  Result.Left := Left;
  Result.Top := Top;
  Result.Width := Width;
  Result.WordWrap := True;
  Result.Caption := Text;
  Result.AdjustHeight;
end;

// Свой цвет текста. seFont снят — иначе цвет шрифта задаёт стиль мастера.
procedure Tint(Text: TNewStaticText; const Color: String);
begin
  Text.StyleElements := Text.StyleElements - [seFont];
  Text.Font.Color := StrToColor(Color);
end;

// Пояснение под галочкой или пунктом: приглушённым цветом окна приложения.
function AddNote(Page: TWizardPage; Left, Top: Integer; const Text: String): TNewStaticText;
begin
  Result := NewText(Page.Surface, Left, Top, Page.SurfaceWidth - Left, Text);
  Tint(Result, '{#ColorDim}');
end;

// Пояснение под пунктом Option. Top — где начнётся следующий пункт.
procedure AddOptionNote(Page: TWizardPage; Option: TControl; const Note: String; var Top: Integer);
var
  Hint: TNewStaticText;
begin
  Hint := AddNote(Page, ScaleX(18), Option.Top + Option.Height + ScaleY(2), Note);
  Top := Hint.Top + Hint.Height + ScaleY(14);
end;

// Галочка задачи жирным и пояснение под ней. Top — где кончился прошлый пункт.
function AddTaskOption(var Top: Integer; const Caption, Note: String): TNewCheckBox;
begin
  Result := TNewCheckBox.Create(TasksPage);
  Result.Parent := TasksPage.Surface;
  Result.Top := Top;
  Result.Width := TasksPage.SurfaceWidth;
  Result.Height := ScaleY(17);
  Result.Caption := Caption;
  Result.Font.Style := [fsBold];
  AddOptionNote(TasksPage, Result, Note, Top);
end;

// Плашка, как карточка окна: фон --card и полоса цвета BarColor слева. Icon
// не пустой — значок того же цвета перед текстом.
function NewPlate(Parent: TWinControl; Width: Integer; const BarColor, Icon, Text: String): TPanel;
var
  Plate, Bar: TPanel;
  Glyph, Body: TNewStaticText;
  X: Integer;
begin
  Plate := TPanel.Create(WizardForm);
  Plate.Parent := Parent;
  Plate.Width := Width;
  Plate.BevelOuter := bvNone;
  // Без стиля мастера: иначе фон панели — его, а не наш.
  Plate.StyleElements := [];
  Plate.ParentBackground := False;
  Plate.Color := StrToColor('{#ColorCard}');
  Bar := TPanel.Create(WizardForm);
  Bar.Parent := Plate;
  Bar.BevelOuter := bvNone;
  Bar.StyleElements := [];
  Bar.ParentBackground := False;
  Bar.Color := StrToColor(BarColor);
  Bar.Align := alLeft;
  Bar.Width := ScaleX(3);
  X := ScaleX(13);
  if Icon <> '' then
  begin
    Glyph := NewText(Plate, X, ScaleY(8), ScaleX(16), Icon);
    Tint(Glyph, BarColor);
    Glyph.Font.Style := [fsBold];
    X := X + ScaleX(18);
  end;
  Body := NewText(Plate, X, ScaleY(8), Plate.Width - X - ScaleX(10), Text);
  Plate.Height := Body.Height + ScaleY(16);
  Result := Plate;
end;

// Плашка сверху страницы Page. Возвращает её низ.
function AddPlate(Page: TWizardPage; const Text: String): Integer;
var
  Plate: TPanel;
begin
  Plate := NewPlate(Page.Surface, Page.SurfaceWidth, '{#ColorOff}', '', Text);
  Result := Plate.Top + Plate.Height;
end;

procedure CreateTasksPage;
var
  Top: Integer;
begin
  TasksPage := CreateCustomPage(wpSelectDir, 'Параметры', 'Что включить вместе с программой');
  Top := 0;
  AutorunBox := AddTaskOption(Top, 'Запускать значок в трее при входе в Windows',
    'Без запроса прав администратора при каждом входе.');
  DesktopBox := AddTaskOption(Top, 'Значок на рабочем столе', 'Ярлык {#MyName} рядом с остальными.');
end;

// '!name' снимает задачу в WizardSelectTasks.
function TaskSpec(const Name: String; Selected: Boolean): String;
begin
  if Selected then
    Result := Name
  else
    Result := '!' + Name;
end;

// Ссылка на «Готово». На GitHub — от исходного пользователя, не от админа.
procedure LinkClick(Sender: TObject; const Link: String; LinkType: TSysLinkType);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', Link, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

// Место — в CurPageChanged: высоту RunList Inno задаёт, только показывая «Готово».
procedure CreateFinishedLink;
begin
  FinishedLink := TNewLinkLabel.Create(WizardForm);
  FinishedLink.Parent := WizardForm.FinishedPage;
  FinishedLink.Left := WizardForm.RunList.Left;
  FinishedLink.Caption := '<a href="{#MyRepo}#readme">Инструкция на GitHub</a>';
  FinishedLink.UseVisualStyle := HighContrastActive;
  FinishedLink.OnLinkClick := @LinkClick;
end;

// Убивает процессы Image вместе с дочерними. True — было что убить.
function KillImage(const Image: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM ' + Image + ' /F /T',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// Был ли поднят туннель у службы с данными в {commonappdata}\Name. Читать до
// остановки службы. status.json пишет служба (probe.py:write_status),
// отступ 1 — "up": true.
function TunnelUp(const Name: String): Boolean;
var
  Status: AnsiString;
begin
  Result := LoadStringFromFile(ExpandConstant('{commonappdata}\') + Name + '\state\status.json',
                               Status) and (Pos('"up": true', Status) > 0);
end;

// net stop, а не sc stop: ждёт, пока служба действительно остановится и
// опустит туннель. True — служба работала и остановилась.
function StopService(const Name: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{sys}\net.exe'), 'stop ' + Name,
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// Закрывает трей, окно и службу: иначе файлы заняты (служба работает из
// {app}\tunnelvpn.exe) и обновление падает на середине, оставляя половину
// старой версии. То же для установки под прежним именем DualVPN: её служба,
// остановившись, сама снимает свои маршруты и правила. CLI убиваем только
// после службы — под этим именем работает и служба, и окно трея.
procedure StopEverything;
var
  LegacyUp: Boolean;
begin
  // /T — вместе с окном, которое трей держит прогретым.
  TrayWasRunning := KillImage('{#MyExe}');
  if KillImage('{#LegacyExe}') then
    TrayWasRunning := True;
  // Служба не работала — status.json старый, туннеля не было.
  TunnelWasUp := TunnelUp('{#MyName}');
  if not StopService('{#MyName}') then
    TunnelWasUp := False;
  LegacyUp := TunnelUp('{#LegacyName}');
  if not StopService('{#LegacyName}') then
    LegacyUp := False;
  TunnelWasUp := TunnelWasUp or LegacyUp;
  KillImage('{#MyCli}');
  KillImage('{#LegacyCli}');
end;

// Запускает шаг установки и пишет в журнал, чем он кончился. True — код 0.
function RunStep(const Exe, Params: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(Exe, Params, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  Log(ExtractFileName(Exe) + ' ' + Params + ': код ' + IntToStr(ResultCode));
end;

// Папка прошлой установки из её записи в «Приложениях»; '' — ставим впервые.
function PreviousAppDir: String;
begin
  if RegQueryStringValue(HKLM64, UninstKey, 'InstallLocation', Result) then
    Result := RemoveBackslashUnlessRoot(Result)
  else
    Result := '';
end;

// Папка установки под прежним именем: AppId общий, и запись в реестре та же.
// Корень диска — никогда: MigrateLegacy удаляет эту папку целиком.
function IsLegacyDir(const Dir: String): Boolean;
begin
  Result := (ExtractFileName(Dir) <> '') and FileExists(Dir + '\{#LegacyCli}');
end;

// DefaultDirName: обновление остаётся в папке, куда TunnelVPN поставили, — и
// не по умолчанию тоже. Папку DualVPN не берём: её чистит MigrateLegacy.
function DefaultAppDir(Param: String): String;
begin
  Result := PreviousAppDir;
  if (Result = '') or IsLegacyDir(Result) then
    Result := ExpandConstant('{autopf}\{#MyName}');
end;

// Отрезает от S число до точки вместе с точкой. Хвост вроде «-check» —
// конец версии: S становится пустой.
function NextVersionPart(var S: String): Integer;
var
  I: Integer;
begin
  I := 1;
  while I <= Length(S) do
  begin
    if (S[I] < '0') or (S[I] > '9') then
      Break;
    I := I + 1;
  end;
  Result := StrToIntDef(Copy(S, 1, I - 1), 0);
  if I > Length(S) then
    S := ''
  else if S[I] = '.' then
    Delete(S, 1, I)
  else
    S := '';
end;

// По числам, а не строкой: '0.10.0' новее '0.9.0'. -1, 0, 1 — A ниже, равна, выше B.
function CompareVersions(A, B: String): Integer;
var
  X, Y: Integer;
begin
  Result := 0;
  while (Result = 0) and ((A <> '') or (B <> '')) do
  begin
    X := NextVersionPart(A);
    Y := NextVersionPart(B);
    if X < Y then
      Result := -1
    else if X > Y then
      Result := 1;
  end;
end;

const
  SvcRunning = 0;
  SvcStopped = 1;
  SvcPending = 2;
  SvcMissing = 3;

// Svc*: работает, остановлена, запускается или останавливается, её нет.
// Через PowerShell, а не разбором вывода sc query: Get-Service отдаёт
// состояние одинаково на любом языке Windows.
function ServiceStatus: Integer;
var
  ResultCode: Integer;
begin
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "$s = (Get-Service {#MyName} -ErrorAction SilentlyContinue).Status; ' +
    'if ($s -eq ''Running'') { exit 0 } elseif ($s -eq ''Stopped'') { exit 1 } elseif ($s) { exit 2 } else { exit 3 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := ResultCode
  else
    Result := SvcMissing;
end;

// Запускает службу и ждёт, пока она заработает. '' — работает, иначе причина
// для страницы ошибки. Её же зовёт «Повторить запуск».
function StartService: String;
var
  I: Integer;
begin
  SetStatus('Запускаю службу…');
  // Уже работающая служба отвечает на start ошибкой 1056 — это не сбой.
  if
#ifdef TESTHOOKS
     TestHook('service') or
#endif
     not RunStep(ExpandConstant('{app}\{#MyCli}'), 'service start') and (ServiceStatus <> SvcRunning) then
  begin
    Result := 'Служба установилась, но не запустилась.';
    Exit;
  end;
  // service start только посылает команду — ждём, пока служба поднимется.
  SetStatus('Проверяю, что служба работает…');
  for I := 1 to 20 do
  begin
    case ServiceStatus of
      SvcRunning:
        begin
          Result := '';
          Exit;
        end;
      // Команду она приняла, значит, упала сама.
      SvcStopped:
        begin
          Result := 'Служба установилась, но остановилась сразу после запуска.';
          Exit;
        end;
    end;
    Sleep(1000);
  end;
  Result := 'Служба запущена, но за 20 секунд так и не заработала. Её мог задержать антивирус ' +
            'или другой VPN-клиент, который держит сетевой адаптер.';
end;

// Ставит и запускает службу, проверяя каждый шаг. '' — служба работает,
// иначе — что именно не вышло, фразой для страницы ошибки.
// Прошлую не снимаем: «service install» у существующей службы сам переходит
// в update (ChangeServiceConfig) и переписывает путь к exe. Снятие перед
// установкой только помечало службу на удаление, если кто-то держал её
// дескриптор, и install падал с 1072 — служба пропадала до перезагрузки.
// Остановил её ещё StopEverything.
function InstallService: String;
var
  Sc: String;
begin
  Sc := ExpandConstant('{sys}\sc.exe');
  SetStatus('Регистрирую службу…');
  if not RunStep(ExpandConstant('{app}\{#MyCli}'), 'service install') then
    Result := 'Службу не удалось зарегистрировать в Windows.'
  // Автозапуск службы: значок в трее без неё бесполезен, а ждать ручного
  // запуска после каждой перезагрузки — не то, чего ждут от VPN-клиента.
  else if not RunStep(Sc, 'config {#MyName} start= auto') then
    Result := 'Не удалось включить автозапуск службы.'
  // Служба переживает падение: маршруты без неё убрать некому.
  else if not RunStep(Sc, 'failure {#MyName} reset= 86400 actions= restart/5000/restart/10000/restart/30000') then
    Result := 'Не удалось настроить перезапуск службы при сбое.'
  else
    Result := StartService;
end;

// После запуска новой службы — как было до обновления. Туннель при
// включённом автоподключении служба поднимет сама. Чем кончилось — в
// TunnelRestore: его показывает «Готово» (ShowFinished), тихой установке —
// код 11. Трей в обычном режиме запускает галочка на последней странице, а
// тихий её не показывает: возвращаем его так же, как при входе, — только
// значок. Трей — и без службы: в нём видно, что она не работает.
procedure RestoreAfterUpdate;
var
  I, ResultCode: Integer;
  Started: Boolean;
begin
  if (ServiceError = '') and TunnelWasUp then
  begin
    if FileExists(ExpandConstant('{commonappdata}\{#MyName}\state\autostart')) then
      TunnelRestore := TunnelAuto
    else
    begin
      SetStatus('Включаю VPN обратно…');
      Started := False;
#ifdef TESTHOOKS
      if not TestHook('tunnel') then
#endif
      // Канал службы поднимается не сразу после service start.
      for I := 1 to 5 do
      begin
        Started := Exec(ExpandConstant('{app}\{#MyCli}'), 'start', '', SW_HIDE,
                        ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
        if Started then
          Break;
        Sleep(3000);
      end;
      if Started then
        TunnelRestore := TunnelBack
      else
      begin
        TunnelRestore := TunnelLost;
        Log('Туннель до обновления был включён, а после сам не поднялся: код ' + IntToStr(ResultCode));
      end;
    end;
  end;
  if TrayWasRunning and WizardSilent then
    Exec(ExpandConstant('{app}\{#MyExe}'), '--background', '', SW_SHOWNORMAL,
         ewNoWait, ResultCode);
end;

// Для адреса новой задачи на GitHub: всё, кроме латиницы, цифр и «-._», —
// %XX байтами UTF-8. Символов за пределами BMP в наших текстах нет.
function UrlEncode(const S: String): String;
var
  I, C: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := Ord(S[I]);
    if ((C >= Ord('0')) and (C <= Ord('9'))) or ((C >= Ord('A')) and (C <= Ord('Z'))) or
       ((C >= Ord('a')) and (C <= Ord('z'))) or (C = Ord('-')) or (C = Ord('.')) or (C = Ord('_')) then
      Result := Result + S[I]
    else if C < $80 then
      Result := Result + Format('%%%.2X', [C])
    else if C < $800 then
      Result := Result + Format('%%%.2X%%%.2X', [$C0 or (C shr 6), $80 or (C and $3F)])
    else
      Result := Result + Format('%%%.2X%%%.2X%%%.2X', [$E0 or (C shr 12),
        $80 or ((C shr 6) and $3F), $80 or (C and $3F)]);
  end;
end;

// Обычная «Готово». Поверх установленной — итог обновления: ● «VPN снова
// включён» или плашка ⚠, если туннель сам не поднялся. Всё столбиком под
// заголовком, с отступами макета: высоты зависят от текста. Перезагрузку
// Inno показывает своим текстом и радиокнопками — её не трогаем.
procedure ShowFinished;
var
  X, W, Y: Integer;
  Restart, Lost: Boolean;
begin
  Restart := WizardForm.YesRadio.Visible;
  Lost := (TunnelRestore = TunnelLost) and not Restart;
  WarnPlate.Visible := Lost;
  OkDot.Visible := (TunnelRestore = TunnelBack) and not Restart;
  FinishedLink.Visible := not Lost and not Restart;
  with WizardForm do
  begin
    FinishedLabel.Visible := not Lost;
    FinishedHeadingLabel.Caption := FinishedHeading;
    FinishedHeadingLabel.AdjustHeight;
    if Restart then
      Exit;
    X := FinishedHeadingLabel.Left;
    W := FinishedHeadingLabel.Width;
    Y := FinishedHeadingLabel.Top + FinishedHeadingLabel.Height + ScaleY(10);
    if Lost then
    begin
      WarnPlate.Top := Y;
      Y := WarnPlate.Top + WarnPlate.Height;
    end
    else
    begin
      FinishedLabel.Caption := FinishedText;
      if TunnelRestore = TunnelBack then
      begin
        FinishedLabel.Caption := 'VPN снова включён. ' + FinishedText;
        OkDot.Left := X;
        OkDot.Top := Y;
        X := X + ScaleX(16);
        W := W - ScaleX(16);
      end
      else if TunnelRestore = TunnelAuto then
        FinishedLabel.Caption := FinishedText + ' VPN включится сам — автоподключение включено.';
      FinishedLabel.Left := X;
      FinishedLabel.Top := Y;
      FinishedLabel.Width := W;
      FinishedLabel.AdjustHeight;
      Y := FinishedLabel.Top + FinishedLabel.Height;
    end;
    // Inno тянет RunList до низа страницы: по высоте галочек — и ссылка под ними.
    RunList.Top := Y + ScaleY(10);
    RunList.Height := RunList.Items.Count * ScaleY(22);
    FinishedLink.Top := RunList.Top + RunList.Height + ScaleY(6);
  end;
end;

// «Готово» — страница ошибки службы или обычная. Свои элементы ставим под
// заголовком: его высота зависит от текста.
procedure ShowServiceError(Failed: Boolean);
var
  X, W, Y, Half: Integer;
begin
  with WizardForm do
  begin
    // На её месте Inno показывает радиокнопки перезагрузки.
    RunList.Visible := not Failed and not YesRadio.Visible;
    // Скрытая галочка всё равно сработала бы на «Готово»: трей без службы
    // открывать незачем.
    if RunList.Items.Count > 0 then
      RunList.Checked[0] := not Failed;
  end;
  if not Failed then
    ShowFinished
  else
    with WizardForm do
    begin
      FinishedHeadingLabel.Caption := 'Служба {#MyName} не запустилась';
      FinishedHeadingLabel.AdjustHeight;
      FinishedLabel.Visible := False;
      FinishedLink.Visible := False;
      OkDot.Visible := False;
      WarnPlate.Visible := False;
      X := FinishedHeadingLabel.Left;
      W := FinishedHeadingLabel.Width;
    end;
  ErrIcon.Visible := Failed;
  ErrReason.Visible := Failed;
  RetryButton.Visible := Failed;
  LogButton.Visible := Failed;
  CopyButton.Visible := Failed;
  ReportButton.Visible := Failed;
  ErrLog.Visible := Failed;
  ErrFoot.Visible := Failed;
  if not Failed then
    Exit;
  Y := WizardForm.FinishedHeadingLabel.Top + WizardForm.FinishedHeadingLabel.Height + ScaleY(14);
  ErrIcon.Left := X;
  ErrIcon.Top := Y - ScaleY(4);
  X := X + ScaleX(26);
  W := W - ScaleX(26);
  ErrReason.Left := X;
  ErrReason.Top := Y;
  ErrReason.Width := W;
  ErrReason.Caption := ServiceError;
  ErrReason.AdjustHeight;
  Y := ErrReason.Top + ErrReason.Height + ScaleY(12);
  Half := (W - ScaleX(8)) div 2;
  RetryButton.SetBounds(X, Y, Half, ScaleY(23));
  LogButton.SetBounds(X + Half + ScaleX(8), Y, Half, ScaleY(23));
  Y := Y + ScaleY(31);
  CopyButton.SetBounds(X, Y, Half, ScaleY(23));
  ReportButton.SetBounds(X + Half + ScaleX(8), Y, Half, ScaleY(23));
  ErrLog.Left := X;
  ErrLog.Top := Y + ScaleY(35);
  ErrLog.Width := W;
  ErrLog.AdjustHeight;
  ErrFoot.Left := X;
  ErrFoot.Top := ErrLog.Top + ErrLog.Height + ScaleY(16);
  ErrFoot.Width := W;
  ErrFoot.AdjustHeight;
end;

// «Повторить запуск»: только запуск и проверка — файлы и регистрация уже есть.
// Помогло — та же страница становится обычной «Готово», и туннель
// возвращается, как после обновления.
procedure RetryClick(Sender: TObject);
begin
  RetryButton.Enabled := False;
  WizardForm.NextButton.Enabled := False;
  ErrReason.Caption := 'Запускаю службу…';
  ServiceError := StartService;
  RetryButton.Enabled := True;
  WizardForm.NextButton.Enabled := True;
  if ServiceError = '' then
  begin
    Log('Служба: заработала после повторного запуска');
    RestoreAfterUpdate;
  end
  else
    Log('Служба (повтор): ' + ServiceError);
  ShowServiceError(ServiceError <> '');
end;

// Журнал и задачу на GitHub открываем от исходного пользователя, не от админа.
procedure OpenLogClick(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', ExpandConstant('{log}'), '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

procedure CopyLogClick(Sender: TObject);
var
  Path: String;
  ResultCode: Integer;
begin
  Path := ExpandConstant('{log}');
  // %TEMP% — в профиле, а в имени пользователя бывает апостроф.
  StringChangeEx(Path, '''', '''''', True);
  if Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
          '-NoProfile -NonInteractive -Command "Set-Clipboard -Value ''' + Path + '''"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    CopyButton.Caption := 'Путь скопирован';
end;

// Версия и Windows — в заголовке задачи. Путь к журналу в адрес не кладём: в
// нём имя пользователя.
procedure ReportClick(Sender: TObject);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', '{#MyRepo}/issues/new?title=' +
    UrlEncode('Служба не запустилась: {#MyName} {#MyVersion}, Windows ' + GetWindowsVersionString) +
    '&body=' + UrlEncode('Причина: ' + ServiceError + #10#10 +
                         'Журнал установки: кнопка «Открыть журнал» в установщике, приложите файл сюда.'),
    '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function NewFinishedButton(const Caption: String; OnClick: TNotifyEvent): TNewButton;
begin
  Result := TNewButton.Create(WizardForm);
  Result.Parent := WizardForm.FinishedPage;
  Result.Caption := Caption;
  Result.OnClick := OnClick;
  Result.Visible := False;
end;

// Элементы страницы ошибки службы: скрыты, пока служба работает.
procedure CreateServiceErrorPage;
var
  Page: TWinControl;
  W: Integer;
begin
  Page := WizardForm.FinishedPage;
  W := WizardForm.FinishedHeadingLabel.Width;
  ErrIcon := NewText(Page, 0, 0, ScaleX(20), '✕');
  Tint(ErrIcon, '{#ColorErr}');
  ErrIcon.Font.Size := 14;
  ErrIcon.AdjustHeight;
  ErrReason := NewText(Page, 0, 0, W, '');
  RetryButton := NewFinishedButton('Повторить запуск', @RetryClick);
  LogButton := NewFinishedButton('Открыть журнал', @OpenLogClick);
  CopyButton := NewFinishedButton('Скопировать путь', @CopyLogClick);
  ReportButton := NewFinishedButton('Сообщить о проблеме', @ReportClick);
  ErrLog := NewText(Page, 0, 0, W, 'Журнал: ' + ExpandConstant('{log}'));
  Tint(ErrLog, '{#ColorDim}');
  ErrFoot := NewText(Page, 0, 0, W, 'Программа скопирована. Когда служба заработает, VPN можно будет включить.');
  ErrIcon.Visible := False;
  ErrReason.Visible := False;
  ErrLog.Visible := False;
  ErrFoot.Visible := False;
end;

// Итог обновления на «Готово» и пояснение к WebView2 на странице установки:
// скрыты, пока не понадобятся.
procedure CreateResultControls;
begin
  OkDot := NewText(WizardForm.FinishedPage, 0, 0, ScaleX(14), '●');
  Tint(OkDot, '{#ColorOk}');
  OkDot.Visible := False;
  WarnPlate := NewPlate(WizardForm.FinishedPage, WizardForm.FinishedHeadingLabel.Width, '{#ColorWarn}', '⚠',
    'Обновление установлено, но VPN не включился сам. Откройте {#MyName} и нажмите «Включить» — ' +
    'если не получится, причина будет в журнале.');
  WarnPlate.Left := WizardForm.FinishedHeadingLabel.Left;
  WarnPlate.Visible := False;
  with WizardForm.ProgressGauge do
    WebView2Note := NewText(WizardForm.InstallingPage, Left, Top + Height + ScaleY(10), Width,
      'Без него не откроется окно {#MyName}. Туннели и значок в трее работают и без него.');
  Tint(WebView2Note, '{#ColorDim}');
  WebView2Note.Visible := False;
end;

// Кнопка «Далее» меню — по выбранному пункту. Пока не выбран ни один (откат
// по умолчанию не выбран), она неактивна.
procedure UpdateActionButtons;
var
  Removing: Boolean;
begin
  Removing := (RemoveRadio <> nil) and RemoveRadio.Checked;
  // Неактивная ссылка в тёмном стиле не отличается от активной — прячем.
  ParamsLink.Visible := InstallRadio.Checked;
  WizardForm.NextButton.Enabled := InstallRadio.Checked or Removing;
  if Removing then
    WizardForm.NextButton.Caption := 'Удалить'
  else if InstallRadio.Checked then
    WizardForm.NextButton.Caption := InstallCaption
  else
    WizardForm.NextButton.Caption := SetupMessage(msgButtonNext);
end;

// Checked := True в CreateActionPage тоже зовёт OnClick — тогда кнопок ещё нет.
procedure ActionChanged(Sender: TObject);
begin
  if WizardForm.CurPageID = ActionPage.ID then
    UpdateActionButtons;
end;

// «Изменить параметры»: та же установка, но через страницу «Параметры».
// Ссылка видна, только пока выбран пункт установки.
procedure ChangeTasksClick(Sender: TObject; const Link: String; LinkType: TSysLinkType);
begin
  ChangeTasks := True;
  WizardForm.NextButton.OnClick(WizardForm.NextButton);
end;

// Папку конфигов показываем выделенной в ProgramData\TunnelVPN, а не
// открываем: conf\ закрыт для пользователя (icacls в [Run]), и Проводник
// предложил бы «получить доступ» — то есть переписать её права.
procedure OpenConfClick(Sender: TObject; const Link: String; LinkType: TSysLinkType);
var
  ErrorCode: Integer;
begin
  ShellExecAsOriginalUser('open', ExpandConstant('{win}\explorer.exe'),
    '/select,"' + ExpandConstant('{commonappdata}\{#MyName}\conf') + '"', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

function AddActionOption(var Top: Integer; const Caption, Note: String): TNewRadioButton;
begin
  Result := TNewRadioButton.Create(ActionPage);
  Result.Parent := ActionPage.Surface;
  Result.Top := Top;
  Result.Width := ActionPage.SurfaceWidth;
  Result.Height := ScaleY(17);
  Result.Caption := Caption;
  Result.Font.Style := [fsBold];
  Result.OnClick := @ActionChanged;
  AddOptionNote(ActionPage, Result, Note, Top);
end;

// Версия без хвоста «-beta»: так её показывает мастер.
function PlainVersion(const Version: String): String;
begin
  Result := Version;
  if Pos('-', Result) > 0 then
    Result := Copy(Result, 1, Pos('-', Result) - 1);
end;

// Повторный запуск setup: обновить (переустановить, откатить) или удалить.
// В тихом режиме страниц нет — ставим этот файл, даже если это откат (так
// ставит процедура из CLAUDE.md), см. NextButtonClick.
procedure CreateActionPage;
var
  OldVersion, Versions: String;
  Cmp, Top: Integer;
  Legacy: Boolean;
  Link: TNewLinkLabel;
begin
  RegQueryStringValue(HKLM64, UninstKey, 'DisplayVersion', OldVersion);
  OldVersion := PlainVersion(OldVersion);
  if OldVersion = '' then
    OldVersion := 'неизвестная версия';
  Legacy := IsLegacyDir(PreviousAppDir);
  if Legacy then
    Versions := 'Установлено: {#LegacyName} ' + OldVersion + ' · В этом файле: {#MyName} {#MyPlainVersion}'
  else
    Versions := 'Установлено: ' + OldVersion + ' · В этом файле: {#MyPlainVersion}';
  ActionPage := CreateCustomPage(wpWelcome, '{#MyName} уже установлен', Versions);
  Top := 0;
  if Legacy then
    Top := AddPlate(ActionPage, 'Прошлая версия называлась {#LegacyName}. Конфиги и ключи перенесём, ' +
                                'старую службу и ярлыки уберём.') + ScaleY(14);
  Cmp := CompareVersions(OldVersion, '{#MyVersion}');
  if Legacy or (Cmp < 0) then
  begin
    InstallCaption := 'Обновить';
    UpdatedHeading := '{#MyName} обновлён до {#MyPlainVersion}';
    FinishedLink.Caption := '<a href="{#MyRepo}/releases/tag/v{#MyVersion}">Что нового</a>';
    InstallRadio := AddActionOption(Top, 'Обновить до {#MyPlainVersion}',
      'Конфиги, правила и настройки сохранятся. VPN отключится на время обновления и включится обратно.');
  end
  else if Cmp = 0 then
  begin
    InstallCaption := 'Переустановить';
    UpdatedHeading := '{#MyName} переустановлен';
    InstallRadio := AddActionOption(Top, 'Переустановить {#MyPlainVersion}',
      'Если что-то сломалось. Конфиги сохранятся.');
  end
  else
  begin
    InstallCaption := 'Откатить';
    UpdatedHeading := '{#MyName} {#MyPlainVersion} установлен';
    // Формат конфигов меняется только вперёд: что записала новая версия,
    // старая может прочитать не так или не прочитать.
    InstallRadio := AddActionOption(Top, '⚠ Откатить до {#MyPlainVersion}',
      'Старая версия может не понять настройки, сохранённые новой. Перед откатом сделайте копию ' +
      ExpandConstant('{commonappdata}\{#MyName}\conf') + '.');
    InstallRadio.StyleElements := InstallRadio.StyleElements - [seFont];
    InstallRadio.Font.Color := StrToColor('{#ColorWarn}');
    Link := TNewLinkLabel.Create(ActionPage);
    Link.Parent := ActionPage.Surface;
    Link.Left := ScaleX(18);
    Link.Top := Top - ScaleY(12);
    Link.Caption := '<a href="conf">Открыть папку</a>';
    Link.UseVisualStyle := HighContrastActive;
    Link.OnLinkClick := @OpenConfClick;
    Top := Link.Top + Link.Height + ScaleY(14);
  end;
  // Откат — только осознанно: пункт не выбран.
  InstallRadio.Checked := Legacy or (Cmp <= 0);
  if not Legacy then
    RemoveRadio := AddActionOption(Top, 'Удалить {#MyName}',
      'Конфиги с ключами останутся, если не попросите удалить.');
  ParamsLink := TNewLinkLabel.Create(ActionPage);
  ParamsLink.Parent := ActionPage.Surface;
  ParamsLink.Caption := '<a href="tasks">Изменить параметры</a>';
  ParamsLink.UseVisualStyle := HighContrastActive;
  ParamsLink.OnLinkClick := @ChangeTasksClick;
  ParamsLink.Top := ActionPage.SurfaceHeight - ParamsLink.Height;
end;

procedure InitializeWizard;
begin
  CreateTasksPage;
  CreateFinishedLink;
  CreateServiceErrorPage;
  CreateResultControls;
  if RegQueryStringValue(HKLM64, UninstKey, 'UninstallString', OldUninstaller) then
    CreateActionPage;
end;

// Данные WebView2 окна (window.py:_storage_path) в %LOCALAPPDATA%\Name.
// Только эти папки, не всю: туда кладёт конфиги портативная версия
// (portable.data_dir, и под прежним именем — её же _adopt).
procedure DeleteWindowData(const Name: String);
var
  Dir: String;
begin
  Dir := ExpandConstant('{localappdata}\') + Name;
  DelTree(Dir + '\WebView2-admin', True, True, True);
  DelTree(Dir + '\WebView2-user', True, True, True);
  RemoveDir(Dir);
end;

// Конфиги по умолчанию переживают удаление: в них ключи, и после
// переустановки человек не обязан их восстанавливать. Но если удаляют совсем,
// оставлять каталог молча — это мусор. Спрашиваем до удаления, двумя
// кнопками-ссылками с пояснениями; по умолчанию «Оставить». Без кнопки
// «Отмена»: удаление уже подтверждено, и отсюда его не прервать. Тихое
// удаление не спрашивает и конфиги оставляет. True — «Удалить навсегда».
function AskPurgeData: Boolean;
begin
  Result := TaskDialogMsgBox('Что сделать с конфигами и ключами?',
    'Папка: ' + ExpandConstant('{commonappdata}\{#MyName}\conf'),
    // Массив не с начала строки: «[» там Inno читает как начало секции.
    mbConfirmation, MB_YESNO, ['Оставить'#13#10'Их подхватит следующая установка {#MyName}.',
      'Удалить навсегда'#13#10'Приватные ключи будут удалены. Восстановить их можно только у того, кто выдал конфиг.'],
    0) = IDNO;
  Log('Конфиги при удалении: ' + IntToStr(Ord(Result)));
end;

// Удаление прежней установкой: её деинсталлятор знает, что она ставила.
// Трей, окно и службу гасим сами, папки окна чистим тоже — деинсталляторы
// до этой версии не делали ни того, ни другого. /SILENT: вопрос про конфиги
// уже задан (AskPurgeData), а прогресс пусть будет виден.
function RunUninstaller(Purge: Boolean): Boolean;
var
  ResultCode: Integer;
begin
  StopEverything;
  Result := Exec(RemoveQuotes(OldUninstaller), '/SILENT /SUPPRESSMSGBOXES /NORESTART',
                 '', SW_SHOW, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
  begin
    MsgBox('Удалить не получилось: деинсталлятор вернул код ' + IntToStr(ResultCode) + '.' + #13#10 + #13#10 +
           'Удалите {#MyName} через «Параметры → Приложения».', mbError, MB_OK);
    Exit;
  end;
  // Удалять могли и установку под прежним именем: её данные — под ним же.
  DeleteWindowData('{#MyName}');
  DeleteWindowData('{#LegacyName}');
  if Purge then
  begin
    DelTree(ExpandConstant('{commonappdata}\{#MyName}'), True, True, True);
    DelTree(ExpandConstant('{commonappdata}\{#LegacyName}'), True, True, True);
  end;
end;

// Стандартная страница задач — вместо неё «Параметры» (TasksPage).
// Приветствие, папка и параметры — только при новой установке: обновление —
// в два клика, в папке прошлой установки, с её задачами (UsePreviousTasks).
// Параметры поверх установленной — по ссылке «Изменить параметры».
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectTasks) or
            ((ActionPage <> nil) and ((PageID = wpWelcome) or (PageID = wpSelectDir) or
                                      ((PageID = TasksPage.ID) and not ChangeTasks)));
end;

// Отказ PrepareToInstall: свои заголовок и текст вместо «Подготовки к
// установке». CurPageChanged(wpPreparing) приходит второй раз уже после
// отказа. С перезагрузкой Inno сам ставит «Перезагрузить сейчас / Позже» и
// «Готово» — их только сдвигаем под текст; без неё остаётся одна «Закрыть».
procedure ShowPrepareError;
begin
  with WizardForm do
  begin
    PageNameLabel.Caption := PrepareTitle;
    PageDescriptionLabel.Caption := PrepareDesc;
    PreparingLabel.Caption := PrepareError;
    PreparingLabel.AdjustHeight;
    PreparingYesRadio.Top := PreparingLabel.Top + PreparingLabel.Height + ScaleY(12);
    PreparingNoRadio.Top := PreparingYesRadio.Top + ScaleY(24);
    BackButton.Visible := False;
    if not PrepareRestart then
    begin
      NextButton.Visible := False;
      CancelButton.Caption := 'Закрыть';
      Leaving := True;
    end;
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (ActionPage <> nil) and (CurPageID = ActionPage.ID) then
  begin
    // Вернулись с «Параметров» — следующее «Обновить» снова без них.
    ChangeTasks := False;
    UpdateActionButtons;
  end
  else if CurPageID = TasksPage.ID then
  begin
    // Задачи прошлой установки Inno уже отметил (UsePreviousTasks).
    AutorunBox.Checked := WizardIsTaskSelected('autorun');
    DesktopBox.Checked := WizardIsTaskSelected('desktopicon');
    // Страницы «Готово к установке» нет: отсюда сразу установка.
    if ActionPage <> nil then
      WizardForm.NextButton.Caption := InstallCaption
    else
      WizardForm.NextButton.Caption := SetupMessage(msgButtonInstall);
  end
  else if (CurPageID = wpPreparing) and (PrepareError <> '') then
    ShowPrepareError
  else if CurPageID = wpFinished then
  begin
    // Раскладку «Готово» Inno делает, только показывая её: свою — после.
    if FinishedHeading = '' then
      with WizardForm do
      begin
        if ActionPage <> nil then
        begin
          FinishedHeading := UpdatedHeading;
          FinishedText := 'Конфиги, правила и настройки на месте.';
        end
        else
        begin
          FinishedHeading := FinishedHeadingLabel.Caption;
          FinishedText := FinishedLabel.Caption;
        end;
      end;
    ShowServiceError(ServiceError <> '');
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Purge: Boolean;
begin
  Result := True;
  if CurPageID = TasksPage.ID then
    WizardSelectTasks(TaskSpec('autorun', AutorunBox.Checked) + ',' +
                      TaskSpec('desktopicon', DesktopBox.Checked));
  if (ActionPage = nil) or (CurPageID <> ActionPage.ID) then
    Exit;
  // Тихий режим ничего не выбирает и не показывает: ставим, даже откат.
  if (RemoveRadio = nil) or not RemoveRadio.Checked then
  begin
    Result := InstallRadio.Checked or WizardSilent;
    Exit;
  end;
  Result := False;
  Purge := AskPurgeData;
  if RunUninstaller(Purge) then
  begin
    if Purge then
      MsgBox('{#MyName} удалён вместе с конфигами и ключами.', mbInformation, MB_OK)
    else
      MsgBox('{#MyName} удалён. Конфиги остались в ' + ExpandConstant('{commonappdata}\{#MyName}\conf') + '.',
             mbInformation, MB_OK);
    Leaving := True;
    WizardForm.Close;
  end;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if Leaving then
    Confirm := False;
end;

// Служба, которую сняли, пока чужой процесс держал её дескриптор, остаётся
// помеченной на удаление до перезагрузки: «service install» поверх неё падает
// с 1072, а [Run] код не проверяет — установка «удалась» без службы.
function ServicePendingDelete: Boolean;
var
  Flag: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SYSTEM\CurrentControlSet\Services\{#MyName}',
                               'DeleteFlag', Flag) and (Flag = 1);
end;

// Версии до 0.2.7 писали в журнал событий, и служба журнала держит
// servicemanager.pyd загруженным до перезагрузки: ни перезаписать его, ни
// удалить нельзя, и установка встала бы на «файл занят». Загруженную DLL можно
// переименовать — убираем её с дороги, а старую копию удалит перезагрузка.
procedure MoveAsideLocked(const AppDir: String);
var
  SmPyd, Old: String;
begin
  SmPyd := AppDir + '\_internal\win32\servicemanager.pyd';
  if FileExists(SmPyd) and not DeleteFile(SmPyd) then
  begin
    Old := SmPyd + '.old';
    DeleteFile(Old);
    if RenameFile(SmPyd, Old) then
    begin
      RestartReplace(Old, '');
      // И папки за ним — иначе после удаления программы и перезагрузки
      // оставались бы пустые _internal\win32. Непустые Windows не тронет.
      RestartReplace(ExtractFileDir(SmPyd), '');
      RestartReplace(ExtractFileDir(ExtractFileDir(SmPyd)), '');
      RestartReplace(AppDir, '');
    end;
  end;
end;

// Переезд с DualVPN — прежнего имени проекта. AppId общий, поэтому установщик
// видит её как прошлую версию, но служба, задача, папки и ярлыки у неё свои,
// и новая установка поверх них ничего не перепишет. StopEverything уже
// остановил её службу (туннель, маршруты, NRPT и правило IPv6 она сняла сама)
// и убил процессы. Правила аварийно умершей службы снимет уже новая служба:
// winnet.LEGACY_NRPT_COMMENT и LEGACY_V6_RULE. '' — можно ставить дальше.
function MigrateLegacy: String;
var
  OldData, NewData, OldApp: String;
  ResultCode: Integer;
begin
  Result := '';
  OldData := ExpandConstant('{commonappdata}\{#LegacyName}');
  NewData := ExpandConstant('{commonappdata}\{#MyName}');
  // Конфиги и ключи первыми: не переехали — прежнюю установку не трогаем,
  // её служба с автозапуском поднимется после перезагрузки как была.
  // Переименование на том же томе сохраняет права conf\ и state\run\.
  if DirExists(OldData) and not DirExists(NewData) and not RenameFile(OldData, NewData) then
  begin
    Result := 'Не удалось перенести данные из ' + OldData + ': их держит другая программа. ' +
              'После перезагрузки {#LegacyName} заработает как раньше — перезагрузите компьютер ' +
              'и запустите установку снова.';
    Exit;
  end;
  Exec(ExpandConstant('{sys}\sc.exe'), 'delete {#LegacyName}',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "{#LegacyName} Tray" /F',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  // Её деинсталлятор не зовём: он снёс бы общую запись AppId в реестре и
  // state\ уже перенесённых данных искал бы по старому пути. Чистим сами.
  // Папка — откуда её ставили (её деинсталлятор снёс бы её целиком так же),
  // а не только Program Files\DualVPN. Если в неё же ставят TunnelVPN — не трогаем.
  OldApp := PreviousAppDir;
  if not IsLegacyDir(OldApp) then
    OldApp := ExpandConstant('{autopf}\{#LegacyName}');
  if DirExists(OldApp) and (CompareText(OldApp, ExpandConstant('{app}')) <> 0) then
  begin
    MoveAsideLocked(OldApp);
    DelTree(OldApp, True, True, True);
  end;
  DelTree(ExpandConstant('{autoprograms}\{#LegacyName}'), True, True, True);
  DeleteFile(ExpandConstant('{autodesktop}\{#LegacyName}.lnk'));
  DeleteWindowData('{#LegacyName}');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  StopEverything;
  Result := MigrateLegacy;
  if Result <> '' then
  begin
    Result := Result + #13#10#13#10 + 'Журнал: ' + ExpandConstant('{log}');
    PrepareTitle := 'Установка не началась';
    PrepareDesc := 'Файлы и настройки не тронуты';
    PrepareError := Result;
    Exit;
  end;

  // Дескриптор мог держать только что убитый трей — даём Windows дочистить.
  for I := 1 to 20 do
  begin
    if not ServicePendingDelete then
      Break;
    Sleep(500);
  end;
  if ServicePendingDelete then
  begin
    // Inno предложит перезагрузку и выйдет с кодом 8.
    NeedsRestart := True;
    PrepareRestart := True;
    Result := 'Windows ещё удаляет прежнюю службу {#MyName}: её держит другая программа. ' +
              'Перезагрузите компьютер и запустите установку снова.';
    PrepareTitle := 'Нужна перезагрузка';
    PrepareDesc := 'Установка не начиналась';
    PrepareError := Result;
    Exit;
  end;

  MoveAsideLocked(ExpandConstant('{app}'));
  Result := '';
end;

// Автозапуск трея при входе. Задача планировщика с наивысшими правами, а не
// ярлык в «Автозагрузке»: трей требует администратора, и оттуда Windows его
// не запускает. Без ограничения по времени (по умолчанию задачу снимают
// через 72 часа) и без остановки при переходе на батарею.
// --background: при входе только значок, панель управления не выскакивает.
procedure RegisterTrayTask;
var
  ResultCode: Integer;
begin
  SetStatus('Включаю значок в трее при входе…');
  Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -ExecutionPolicy Bypass -Command "' +
    '$u = [Security.Principal.WindowsIdentity]::GetCurrent().Name; ' +
    '$a = New-ScheduledTaskAction -Execute ''' + ExpandConstant('{app}\{#MyExe}') +
    ''' -Argument ''--background''; ' +
    '$t = New-ScheduledTaskTrigger -AtLogOn -User $u; ' +
    '$p = New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Highest; ' +
    '$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) ' +
    '-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew; ' +
    'Register-ScheduledTask -TaskName ''{#MyName} Tray'' -Action $a -Trigger $t ' +
    '-Principal $p -Settings $s -Force | Out-Null"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

// WebView2 Runtime: без него окно не откроется, только значок в трее. В
// Windows 11 он есть всегда, в Windows 10 — не всегда (LTSC, вычищенные
// сборки). Ключ и значение pv — из документации Microsoft по развёртыванию:
// машинная установка пишет в 32-битную ветку HKLM, пользовательская — в HKCU.
function WebView2Installed: Boolean;
var
  Key, Version: String;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := (RegQueryStringValue(HKLM32, Key, 'pv', Version) and
             (Version <> '') and (Version <> '0.0.0.0')) or
            (RegQueryStringValue(HKCU, Key, 'pv', Version) and
             (Version <> '') and (Version <> '0.0.0.0'));
#ifdef TESTHOOKS
  if WebView2Hook then
    Result := False;
#endif
end;

// Скачанное запускаем с правами администратора — только с действующей
// подписью Microsoft: HTTPS защищает канал, а не то, что лежит в {tmp}.
function SignedByMicrosoft(FileName: String): Boolean;
var
  ResultCode: Integer;
begin
  // {tmp} — в профиле, а в имени пользователя бывает апостроф.
  StringChangeEx(FileName, '''', '''''', True);
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -Command "$s = Get-AuthenticodeSignature -LiteralPath ''' + FileName + '''; ' +
    'if ($s.Status -eq ''Valid'' -and $s.SignerCertificate.Subject -like ''*O=Microsoft Corporation*'') ' +
    '{ exit 0 } else { exit 1 }"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

// Мегабайты с одним знаком после запятой, «2», а не «2,0».
function Megabytes(Bytes: Int64): String;
var
  Tenths: Int64;
begin
  Tenths := Bytes * 10 div 1048576;
  Result := IntToStr(Tenths div 10);
  if Tenths mod 10 <> 0 then
    Result := Result + ',' + IntToStr(Tenths mod 10);
end;

// Загрузка WebView2: сколько скачано — в строке этапа и на полосе прогресса.
function WebView2Progress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  if ProgressMax > 0 then
  begin
    WizardForm.StatusLabel.Caption := 'Скачиваю Microsoft WebView2 — ' + Megabytes(Progress) +
                                      ' из ' + Megabytes(ProgressMax) + ' МБ';
    WizardForm.ProgressGauge.Position := Progress * 100 div ProgressMax;
  end
  else
    WizardForm.StatusLabel.Caption := 'Скачиваю Microsoft WebView2 — ' + Megabytes(Progress) + ' МБ';
  Result := True;
end;

// Одна попытка поставить WebView2 загрузчиком Microsoft (он сам скачивает
// Runtime под систему). '' — стоит, иначе причина для окна.
function TryWebView2: String;
var
  Setup: String;
begin
  SetStatus('Скачиваю Microsoft WebView2…');
  WizardForm.ProgressGauge.Position := 0;
  try
    DownloadTemporaryFile(WebView2Url, 'MicrosoftEdgeWebview2Setup.exe', '', @WebView2Progress);
  except
    Log('WebView2: не скачался — ' + GetExceptionMessage);
    Result := 'Загрузчик не скачался с сайта Microsoft.';
    Exit;
  end;
  Setup := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  if not SignedByMicrosoft(Setup) then
  begin
    Result := 'У скачанного загрузчика нет действующей подписи Microsoft — запускать его небезопасно.';
    Exit;
  end;
  // Сколько качает сам загрузчик, он не сообщает: полоса бегущая.
  SetStatus('Устанавливаю Microsoft WebView2…');
  WizardForm.ProgressGauge.Style := npbstMarquee;
  RunStep(Setup, '/silent /install');
  WizardForm.ProgressGauge.Style := npbstNormal;
  if WebView2Installed then
    Result := ''
  else
    Result := 'Загрузчик Microsoft не смог его поставить.';
end;

// WebView2 Runtime нужен только окну: не поставился — служба, туннели и трей
// работают. Спрашиваем, повторить ли; тихая установка без окон идёт дальше
// и сообщает кодом 12.
procedure EnsureWebView2;
var
  Reason: String;
  OldMax, OldPosition: Integer;
begin
#ifdef TESTHOOKS
  WebView2Hook := TestHook('webview2');
#endif
  if WebView2Installed then
    Exit;
  OldMax := WizardForm.ProgressGauge.Max;
  OldPosition := WizardForm.ProgressGauge.Position;
  WizardForm.ProgressGauge.Max := 100;
  WebView2Note.Visible := True;
  repeat
    Reason := TryWebView2;
#ifdef TESTHOOKS
    WebView2Hook := False;
#endif
    if Reason <> '' then
      Log('WebView2: ' + Reason);
  until (Reason = '') or (SuppressibleTaskDialogMsgBox('Не удалось поставить WebView2',
    Reason + #13#10#13#10 + '{#MyName} установится, но окно не откроется, пока компонента нет. ' +
    'Туннели и значок в трее работают.' + #13#10#13#10 + 'Поставить вручную: ' + WebView2Url,
    // Массив не с начала строки: «[» там Inno читает как начало секции.
    mbError, MB_RETRYCANCEL, ['Повторить', 'Продолжить без него'], 0, IDCANCEL) <> IDRETRY);
  WebView2Missing := Reason <> '';
  WebView2Note.Visible := False;
  WizardForm.ProgressGauge.Max := OldMax;
  WizardForm.ProgressGauge.Position := OldPosition;
end;

// Те же шаги, что [UninstallRun], в том же порядке (трей, туннель, служба,
// окно), но с этапами в строке над прогрессом: [UninstallRun] этапов не
// показывает. Он идёт следом и проходит вхолостую.
procedure StopForUninstall;
var
  ResultCode: Integer;
begin
  SetStatus('Отключаю VPN…');
  KillImage('{#MyExe}');
  Exec(ExpandConstant('{app}\{#MyCli}'), 'stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  SetStatus('Останавливаю службу…');
  StopService('{#MyName}');
  KillImage('{#MyCli}');
  SetStatus('Удаляю файлы…');
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if not UninstallSilent then
      PurgeData := AskPurgeData;
    StopForUninstall;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    DeleteWindowData('{#MyName}');
    // После файлов: если удаление сорвётся раньше, ключи останутся.
    if PurgeData then
      DelTree(ExpandConstant('{commonappdata}\{#MyName}'), True, True, True);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Сбой показывает «Готово», перестроенная в страницу ошибки
    // (ShowServiceError); тихая установка узнаёт о нём по коду 10.
    ServiceError := InstallService;
    if ServiceError <> '' then
      Log('Служба: ' + ServiceError);
    RestoreAfterUpdate;
    // После службы и туннеля: загрузка Runtime долгая, а соединение и так
    // лежит с начала установки.
    EnsureWebView2;
    if WizardIsTaskSelected('autorun') then
      RegisterTrayTask
    else
      // Галочку сняли при обновлении — убираем задачу прошлой установки.
      Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "{#MyName} Tray" /F',
           '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

// Тихая установка (процедура в CLAUDE.md) узнаёт о сбоях по коду, а не по
// «Готово»: 10 — файлы на месте, а служба не работает; 11 — служба работает,
// а туннель, включённый до обновления, сам не поднялся; 12 — не поставился
// WebView2. Сбоев несколько — код первого по списку. 1–8 заняты самим Inno.
function GetCustomSetupExitCode: Integer;
begin
  if ServiceError <> '' then
    Result := 10
  else if TunnelRestore = TunnelLost then
    Result := 11
  else if WebView2Missing then
    Result := 12
  else
    Result := 0;
end;
