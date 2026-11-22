using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace OraScrap
{
    sealed class MainForm : Form
    {
        const string HealthUrl = "http://127.0.0.1:5001/api/health";
        const string HomeUrl = "http://127.0.0.1:5001/";

        readonly WebView2 web = new WebView2();
        readonly NotifyIcon tray;
        readonly Label splash;
        Process server;
        bool quitting;

        public MainForm()
        {
            Text = "OraScrapInAll";
            Width = 1280;
            Height = 840;
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(900, 600);
            BackColor = Color.FromArgb(24, 24, 28);
            Icon = SystemIcons.Application;

            splash = new Label
            {
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleCenter,
                ForeColor = Color.WhiteSmoke,
                Font = new Font("Segoe UI", 14f),
                Text = "Démarrage d'OraScrapInAll…"
            };
            Controls.Add(splash);

            web.Dock = DockStyle.Fill;
            web.Visible = false;
            Controls.Add(web);

            tray = new NotifyIcon
            {
                Icon = SystemIcons.Application,
                Text = "OraScrapInAll",
                Visible = true
            };
            tray.DoubleClick += (s, e) => ShowFromTray();
            var menu = new ContextMenuStrip();
            menu.Items.Add("Afficher", null, (s, e) => ShowFromTray());
            menu.Items.Add("Quitter", null, (s, e) => Quit());
            tray.ContextMenuStrip = menu;

            FormClosing += MainForm_FormClosing;
            Shown += async (s, e) => await BootAsync();
        }

        async Task BootAsync()
        {
            try
            {
                Program.Log("démarrage fenêtre");
                if (!IsHealthy())
                {
                    if (PortOpen())
                    {
                        Fail("Le port 5001 est déjà pris par une ancienne instance.\r\nFermez-la, puis relancez.");
                        return;
                    }
                    SetSplash("Démarrage du serveur…");
                    if (!StartServer())
                        return;
                    if (!await WaitHealthyAsync())
                    {
                        Fail("L'application ne répond pas.\r\nLe détail est dans data\\orascrap.log.");
                        return;
                    }
                }

                SetSplash("Ouverture de l'interface…");
                await InitWebAsync();
                web.Source = new Uri(HomeUrl);
                splash.Visible = false;
                web.Visible = true;
                tray.Text = "OraScrapInAll";
            }
            catch (Exception ex)
            {
                Fail(ex.Message);
            }
        }

        async Task InitWebAsync()
        {
            string userData = Path.Combine(Program.Root(), "data", "webview");
            Directory.CreateDirectory(userData);
            var env = await CoreWebView2Environment.CreateAsync(null, userData);
            await web.EnsureCoreWebView2Async(env);
            web.CoreWebView2.Settings.AreDefaultContextMenusEnabled = true;
            web.CoreWebView2.Settings.AreDevToolsEnabled = false;
            web.CoreWebView2.Settings.IsStatusBarEnabled = false;
            web.CoreWebView2.NewWindowRequested += (s, e) =>
            {
                e.Handled = true;
                string url = e.Uri ?? "";
                if (url.StartsWith("http://127.0.0.1:5001", StringComparison.OrdinalIgnoreCase)
                    || url.StartsWith("https://127.0.0.1:5001", StringComparison.OrdinalIgnoreCase))
                {
                    web.CoreWebView2.Navigate(url);
                }
                else if (url.StartsWith("http://", StringComparison.OrdinalIgnoreCase)
                         || url.StartsWith("https://", StringComparison.OrdinalIgnoreCase))
                {
                    try { Process.Start(url); } catch { }
                }
            };
            web.CoreWebView2.DownloadStarting += (s, e) =>
            {
                e.ResultFilePath = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                    "Downloads",
                    Path.GetFileName(e.ResultFilePath));
                e.Handled = false;
            };
        }

        bool StartServer()
        {
            string root = Program.Root();
            string python = Path.Combine(root, "runtime", "python", "python.exe");
            string pythonw = Path.Combine(root, "runtime", "python", "pythonw.exe");
            string app = Path.Combine(root, "app", "app.py");
            if (!File.Exists(pythonw) || !File.Exists(app))
            {
                Fail("Python portable introuvable.\r\nLe dossier runtime doit rester à côté de OraScrap.exe.");
                return false;
            }
            if (!ImportsOk(python))
            {
                SetSplash("Installation des bibliothèques…");
                if (!Install(python, root))
                {
                    Fail("L'installation des bibliothèques a échoué.\r\nLe détail est dans data\\orascrap.log.");
                    return false;
                }
            }
            server = Process.Start(Hidden(pythonw, "\"" + app + "\"", root, false));
            return server != null;
        }

        async Task<bool> WaitHealthyAsync()
        {
            for (int i = 0; i < 80; i++)
            {
                if (server != null && server.HasExited)
                    return false;
                if (IsHealthy())
                    return true;
                await Task.Delay(250);
            }
            return false;
        }

        void SetSplash(string text)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => SetSplash(text)));
                return;
            }
            splash.Text = text;
            tray.Text = text.Length > 60 ? text.Substring(0, 60) : text;
        }

        void Fail(string message)
        {
            Program.Log("erreur : " + message.Replace("\r\n", " "));
            if (InvokeRequired)
            {
                BeginInvoke(new Action(() => Fail(message)));
                return;
            }
            splash.Text = "Erreur";
            MessageBox.Show(this, message, "OraScrapInAll", MessageBoxButtons.OK, MessageBoxIcon.Error);
        }

        void ShowFromTray()
        {
            Show();
            WindowState = FormWindowState.Normal;
            Activate();
        }

        protected override void WndProc(ref Message m)
        {
            if (m.Msg == NativeMethods.WmShowOraScrap)
            {
                ShowFromTray();
                return;
            }
            base.WndProc(ref m);
        }

        void MainForm_FormClosing(object sender, FormClosingEventArgs e)
        {
            if (quitting)
                return;
            if (e.CloseReason == CloseReason.UserClosing)
            {
                e.Cancel = true;
                Hide();
                tray.ShowBalloonTip(2500, "OraScrapInAll", "L'application reste active près de l'horloge.", ToolTipIcon.Info);
                return;
            }
            Quit();
        }

        void Quit()
        {
            if (quitting)
                return;
            quitting = true;
            tray.Visible = false;
            try
            {
                if (server != null && !server.HasExited)
                    server.Kill();
            }
            catch { }
            Application.Exit();
        }

        internal static bool IsHealthy()
        {
            try
            {
                var request = (HttpWebRequest)WebRequest.Create(HealthUrl);
                request.Timeout = 1500;
                request.Method = "GET";
                request.Proxy = null;
                using (var response = (HttpWebResponse)request.GetResponse())
                    return (int)response.StatusCode == 200;
            }
            catch (Exception ex)
            {
                Program.Log("santé : " + ex.Message);
                return false;
            }
        }

        static bool PortOpen()
        {
            try
            {
                using (var client = new System.Net.Sockets.TcpClient())
                {
                    var wait = client.BeginConnect("127.0.0.1", 5001, null, null);
                    if (!wait.AsyncWaitHandle.WaitOne(400))
                        return false;
                    client.EndConnect(wait);
                    return true;
                }
            }
            catch { return false; }
        }

        static bool ImportsOk(string python)
        {
            var check = Process.Start(Hidden(
                python,
                "-c \"import flask,waitress,pandas,openpyxl,selenium,pymupdf\"",
                Program.Root(),
                true));
            if (check == null || !check.WaitForExit(20000))
                return false;
            return check.ExitCode == 0;
        }

        static bool Install(string python, string root)
        {
            var pip = Process.Start(Hidden(
                python,
                "-m pip install -r \"" + Path.Combine(root, "requirements.txt") + "\"",
                root,
                true));
            if (pip == null)
                return false;
            AppendLog(pip);
            pip.WaitForExit();
            return pip.ExitCode == 0;
        }

        static ProcessStartInfo Hidden(string file, string args, string work, bool redirect)
        {
            return new ProcessStartInfo
            {
                FileName = file,
                Arguments = args,
                WorkingDirectory = work,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = redirect,
                RedirectStandardError = redirect
            };
        }

        static void AppendLog(Process process)
        {
            string path = Path.Combine(Program.Root(), "data", "orascrap.log");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            process.OutputDataReceived += (s, e) =>
            {
                if (e.Data != null)
                    File.AppendAllText(path, e.Data + "\r\n");
            };
            process.ErrorDataReceived += (s, e) =>
            {
                if (e.Data != null)
                    File.AppendAllText(path, e.Data + "\r\n");
            };
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                tray.Dispose();
                web.Dispose();
            }
            base.Dispose(disposing);
        }
    }
}
