// Lance OraScrapInAll sans fenêtre de console.
// Double-clic : démarre le serveur, ouvre le navigateur, reste près de l'horloge.
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Threading;
using System.Windows.Forms;

static class Program
{
    const string HealthUrl = "http://127.0.0.1:5001/api/health";
    const string HomeUrl = "http://127.0.0.1:5001/";

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        bool createdNew;
        Mutex mutex = new Mutex(true, "Local\\OraScrapInAll", out createdNew);
        if (!createdNew)
        {
            if (IsHealthy())
                OpenBrowser();
            else
                MessageBox.Show(
                    "OraScrapInAll est déjà en cours de démarrage.",
                    "OraScrapInAll",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            return;
        }
        try
        {
            Application.Run(new TrayApp());
        }
        finally
        {
            mutex.ReleaseMutex();
            mutex.Dispose();
        }
    }

    internal static string Root()
    {
        return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    }

    internal static bool IsHealthy()
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(HealthUrl);
            request.Timeout = 1500;
            request.Method = "GET";
            request.Proxy = null;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                return (int)response.StatusCode == 200;
        }
        catch (Exception ex)
        {
            Log("santé : " + ex.Message);
            return false;
        }
    }

    internal static void Log(string line)
    {
        try
        {
            string folder = Path.Combine(Root(), "data");
            Directory.CreateDirectory(folder);
            File.AppendAllText(Path.Combine(folder, "launcher.log"), DateTime.Now.ToString("HH:mm:ss ") + line + "\r\n");
        }
        catch
        {
        }
    }

    internal static void OpenBrowser()
    {
        Process.Start(HomeUrl);
    }
}

sealed class TrayApp : ApplicationContext
{
    readonly NotifyIcon icon;
    readonly Control invoker = new Control();
    Process server;

    public TrayApp()
    {
        IntPtr handle = invoker.Handle;
        if (handle == IntPtr.Zero)
            throw new InvalidOperationException("Fenêtre interne indisponible.");
        icon = new NotifyIcon();
        icon.Icon = System.Drawing.SystemIcons.Application;
        icon.Visible = true;
        icon.Text = "OraScrapInAll";
        MenuItem open = new MenuItem("Ouvrir", delegate { Program.OpenBrowser(); });
        MenuItem quit = new MenuItem("Quitter", delegate { Quit(); });
        icon.ContextMenu = new ContextMenu(new MenuItem[] { open, quit });
        icon.DoubleClick += delegate { Program.OpenBrowser(); };
        Thread worker = new Thread(Start);
        worker.IsBackground = true;
        worker.Start();
    }

    void OnUi(MethodInvoker action)
    {
        if (invoker.IsDisposed)
            return;
        invoker.BeginInvoke(action);
    }

    void SetStatus(string text)
    {
        if (text.Length > 63)
            text = text.Substring(0, 63);
        OnUi(delegate { icon.Text = text; });
    }

    void Start()
    {
        try
        {
            Program.Log("démarrage");
            if (Program.IsHealthy())
            {
                Program.Log("déjà ouvert");
                Program.OpenBrowser();
                OnUi(delegate { ExitThread(); });
                return;
            }
            if (PortOpen())
            {
                Fail("Le port 5001 est déjà pris par une ancienne fenêtre.\r\nFermez cette fenêtre OraScrapInAll, puis relancez.");
                return;
            }
            string root = Program.Root();
            string python = Path.Combine(root, "runtime", "python", "python.exe");
            string pythonw = Path.Combine(root, "runtime", "python", "pythonw.exe");
            string app = Path.Combine(root, "app", "app.py");
            if (!File.Exists(pythonw) || !File.Exists(app))
            {
                Fail("Python portable introuvable.\r\nLe dossier runtime doit rester à côté de OraScrap.exe.");
                return;
            }
            if (!ImportsOk(python))
            {
                SetStatus("OraScrapInAll — installation…");
                if (!Install(python, root))
                {
                    Fail("L'installation des bibliothèques a échoué.\r\nLe détail est dans data\\orascrap.log.");
                    return;
                }
            }
            SetStatus("OraScrapInAll — démarrage…");
            server = Process.Start(Hidden(pythonw, "\"" + app + "\"", root, false));
            for (int i = 0; i < 80; i++)
            {
                if (server.HasExited)
                {
                    Fail("Le serveur s'est arrêté tout de suite.\r\nLe détail est dans data\\orascrap.log.");
                    return;
                }
                if (Program.IsHealthy())
                {
                    SetStatus("OraScrapInAll");
                    Program.OpenBrowser();
                    return;
                }
                Thread.Sleep(250);
            }
            Fail("L'application ne répond pas.\r\nLe détail est dans data\\orascrap.log.");
        }
        catch (Exception ex)
        {
            Fail(ex.Message);
        }
    }

    static bool PortOpen()
    {
        try
        {
            System.Net.Sockets.TcpClient client = new System.Net.Sockets.TcpClient();
            IAsyncResult wait = client.BeginConnect("127.0.0.1", 5001, null, null);
            if (!wait.AsyncWaitHandle.WaitOne(400))
            {
                client.Close();
                return false;
            }
            try
            {
                client.EndConnect(wait);
                return true;
            }
            catch
            {
                return false;
            }
            finally
            {
                client.Close();
            }
        }
        catch
        {
            return false;
        }
    }

    static bool ImportsOk(string python)
    {
        Process check = Process.Start(Hidden(
            python,
            "-c \"import flask,waitress,pandas,openpyxl,selenium,pymupdf\"",
            Program.Root(),
            true));
        if (!check.WaitForExit(20000))
            return false;
        return check.ExitCode == 0;
    }

    static bool Install(string python, string root)
    {
        Process pip = Process.Start(Hidden(
            python,
            "-m pip install -r \"" + Path.Combine(root, "requirements.txt") + "\"",
            root,
            true));
        AppendLog(pip);
        pip.WaitForExit();
        return pip.ExitCode == 0;
    }

    static ProcessStartInfo Hidden(string file, string args, string work, bool redirect)
    {
        ProcessStartInfo info = new ProcessStartInfo();
        info.FileName = file;
        info.Arguments = args;
        info.WorkingDirectory = work;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = redirect;
        info.RedirectStandardError = redirect;
        return info;
    }

    static void AppendLog(Process process)
    {
        string path = Path.Combine(Program.Root(), "data", "orascrap.log");
        Directory.CreateDirectory(Path.GetDirectoryName(path));
        process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null)
                File.AppendAllText(path, e.Data + "\r\n");
        };
        process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
        {
            if (e.Data != null)
                File.AppendAllText(path, e.Data + "\r\n");
        };
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
    }

    void Fail(string message)
    {
        OnUi(delegate
        {
            icon.Text = "OraScrapInAll — erreur";
            icon.ShowBalloonTip(6000, "OraScrapInAll", message.Replace("\r\n", " "), ToolTipIcon.Error);
            MessageBox.Show(message, "OraScrapInAll", MessageBoxButtons.OK, MessageBoxIcon.Error);
        });
    }

    void Quit()
    {
        icon.Visible = false;
        try
        {
            if (server != null && !server.HasExited)
                server.Kill();
        }
        catch
        {
        }
        ExitThread();
    }
}
