// Installateur mono-fichier : extrait l'archive collée à la fin de cet exe,
// puis lance OraScrap.exe depuis %LOCALAPPDATA%\OraScrapInAll.
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Threading;
using System.Windows.Forms;

static class Program
{
    static readonly byte[] Marker = Encoding.ASCII.GetBytes("###ORASCRAP_ZIP_PAYLOAD###");
    const string AppName = "OraScrapInAll";

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        try
        {
            string installDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                AppName);
            string exePath = Path.Combine(installDir, "OraScrap.exe");
            string packagedVersion = PackagedVersion.Value;
            string installedVersion = ReadText(Path.Combine(installDir, "VERSION"));

            bool needExtract = !File.Exists(exePath)
                || IsNewer(packagedVersion, installedVersion)
                || !Directory.Exists(Path.Combine(installDir, "runtime", "python"));

            if (needExtract)
            {
                using (var ui = new ProgressForm())
                {
                    ui.Show();
                    Application.DoEvents();
                    ui.SetStatus("Préparation…");
                    long offset = FindPayloadOffset(Application.ExecutablePath);
                    if (offset < 0)
                    {
                        MessageBox.Show(
                            "Ce fichier d'installation est incomplet.\r\nRe-téléchargez OraScrapInAll.",
                            AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
                        return;
                    }
                    string tempZip = Path.Combine(Path.GetTempPath(), "OraScrapInAll-payload.zip");
                    ui.SetStatus("Lecture du paquet…");
                    ExtractPayload(Application.ExecutablePath, offset, tempZip, ui);
                    ui.SetStatus("Installation…");
                    InstallZip(tempZip, installDir, ui);
                    try { File.Delete(tempZip); } catch { }
                    CreateShortcut(exePath);
                    ui.SetStatus("Lancement…");
                    Application.DoEvents();
                }
            }

            if (!File.Exists(exePath))
            {
                MessageBox.Show("Installation incomplète : OraScrap.exe introuvable.", AppName,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }
            Process.Start(new ProcessStartInfo
            {
                FileName = exePath,
                WorkingDirectory = installDir,
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, AppName, MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    static string ReadText(string path)
    {
        try { return File.Exists(path) ? File.ReadAllText(path).Trim() : ""; }
        catch { return ""; }
    }

    static bool IsNewer(string remote, string local)
    {
        if (string.IsNullOrEmpty(remote))
            return false;
        if (string.IsNullOrEmpty(local))
            return true;
        return CompareVersion(remote) > CompareVersion(local);
    }

    static Version CompareVersion(string raw)
    {
        raw = (raw ?? "0").Trim().TrimStart('v', 'V');
        Version v;
        return Version.TryParse(raw, out v) ? v : new Version(0, 0, 0);
    }

    static long FindPayloadOffset(string path)
    {
        const int chunk = 1024 * 1024;
        using (var fs = File.OpenRead(path))
        {
            long size = fs.Length;
            if (size < Marker.Length + 100)
                return -1;
            // Cherche le marqueur depuis la fin (le zip est collé après l'exe).
            long pos = Math.Max(0, size - chunk - Marker.Length);
            var buffer = new byte[chunk + Marker.Length];
            while (true)
            {
                fs.Position = pos;
                int read = fs.Read(buffer, 0, buffer.Length);
                for (int i = 0; i <= read - Marker.Length; i++)
                {
                    bool ok = true;
                    for (int j = 0; j < Marker.Length; j++)
                    {
                        if (buffer[i + j] != Marker[j]) { ok = false; break; }
                    }
                    if (ok)
                        return pos + i + Marker.Length;
                }
                if (pos == 0)
                    return -1;
                pos = Math.Max(0, pos - chunk);
            }
        }
    }

    static void ExtractPayload(string selfPath, long offset, string destZip, ProgressForm ui)
    {
        using (var input = File.OpenRead(selfPath))
        using (var output = File.Create(destZip))
        {
            input.Position = offset;
            long total = input.Length - offset;
            var buffer = new byte[1024 * 256];
            long done = 0;
            int n;
            while ((n = input.Read(buffer, 0, buffer.Length)) > 0)
            {
                output.Write(buffer, 0, n);
                done += n;
                if (total > 0)
                    ui.SetProgress((int)(done * 40 / total), "Lecture du paquet…");
            }
        }
    }

    static void InstallZip(string zipPath, string installDir, ProgressForm ui)
    {
        string staging = installDir + ".staging-" + Process.GetCurrentProcess().Id;
        if (Directory.Exists(staging))
            Directory.Delete(staging, true);
        Directory.CreateDirectory(staging);

        using (var zip = ZipFile.OpenRead(zipPath))
        {
            int total = zip.Entries.Count;
            int i = 0;
            foreach (var entry in zip.Entries)
            {
                i++;
                string name = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                if (string.IsNullOrEmpty(name) || name.EndsWith(Path.DirectorySeparatorChar.ToString()))
                    continue;
                // Sécurité : pas de sortie du dossier cible.
                string target = Path.GetFullPath(Path.Combine(staging, name));
                if (!target.StartsWith(Path.GetFullPath(staging) + Path.DirectorySeparatorChar)
                    && !string.Equals(target, Path.GetFullPath(staging), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Archive refusée : " + name);
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                if (entry.Length > 0 || !string.IsNullOrEmpty(Path.GetFileName(target)))
                    entry.ExtractToFile(target, true);
                if (total > 0 && i % 20 == 0)
                    ui.SetProgress(40 + (int)(i * 55L / total), "Installation… " + Path.GetFileName(name));
            }
        }

        // Conserve data/ (historique, réglages, webview) puis remplace le reste.
        string keepData = Path.Combine(installDir, "data");
        string tempData = null;
        if (Directory.Exists(keepData))
        {
            tempData = Path.Combine(Path.GetTempPath(), "OraScrapInAll-data-" + Process.GetCurrentProcess().Id);
            if (Directory.Exists(tempData))
                Directory.Delete(tempData, true);
            Directory.Move(keepData, tempData);
        }

        if (Directory.Exists(installDir))
        {
            try
            {
                Directory.Delete(installDir, true);
            }
            catch
            {
                CopyInto(staging, installDir);
                Directory.Delete(staging, true);
                RestoreData(tempData, installDir);
                ui.SetProgress(100, "Terminé");
                return;
            }
        }

        Directory.Move(staging, installDir);
        RestoreData(tempData, installDir);
        ui.SetProgress(100, "Terminé");
    }

    static void RestoreData(string tempData, string installDir)
    {
        if (tempData == null)
            return;
        string dest = Path.Combine(installDir, "data");
        try
        {
            if (Directory.Exists(dest))
                Directory.Delete(dest, true);
            Directory.Move(tempData, dest);
        }
        catch
        {
            try { Directory.Delete(tempData, true); } catch { }
        }
    }

    static void CopyInto(string src, string dst)
    {
        Directory.CreateDirectory(dst);
        foreach (string dir in Directory.GetDirectories(src, "*", SearchOption.AllDirectories))
        {
            string rel = dir.Substring(src.Length).TrimStart('\\', '/');
            Directory.CreateDirectory(Path.Combine(dst, rel));
        }
        foreach (string file in Directory.GetFiles(src, "*", SearchOption.AllDirectories))
        {
            string rel = file.Substring(src.Length).TrimStart('\\', '/');
            string target = Path.Combine(dst, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(target));
            File.Copy(file, target, true);
        }
    }

    static void CreateShortcut(string targetExe)
    {
        try
        {
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            string lnk = Path.Combine(desktop, AppName + ".lnk");
            string script = Path.Combine(Path.GetTempPath(), "orascrap-shortcut.ps1");
            File.WriteAllText(script,
                "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('" + lnk.Replace("'", "''") + "')\r\n" +
                "$s.TargetPath = '" + targetExe.Replace("'", "''") + "'\r\n" +
                "$s.WorkingDirectory = '" + Path.GetDirectoryName(targetExe).Replace("'", "''") + "'\r\n" +
                "$s.Description = 'OraScrapInAll'\r\n" +
                "$s.Save()\r\n", Encoding.UTF8);
            var ps = Process.Start(new ProcessStartInfo
            {
                FileName = "powershell",
                Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\"",
                CreateNoWindow = true,
                UseShellExecute = false
            });
            if (ps != null)
                ps.WaitForExit(15000);
        }
        catch { }
    }
}

sealed class ProgressForm : Form
{
    readonly Label label = new Label();
    readonly ProgressBar bar = new ProgressBar();

    public ProgressForm()
    {
        Text = "OraScrapInAll";
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MaximizeBox = false;
        MinimizeBox = false;
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(420, 110);
        ControlBox = false;
        label.AutoSize = false;
        label.SetBounds(16, 16, 388, 28);
        label.Text = "Installation…";
        bar.SetBounds(16, 56, 388, 24);
        bar.Minimum = 0;
        bar.Maximum = 100;
        Controls.Add(label);
        Controls.Add(bar);
    }

    public void SetStatus(string text)
    {
        label.Text = text;
        Application.DoEvents();
    }

    public void SetProgress(int value, string text)
    {
        if (value < 0) value = 0;
        if (value > 100) value = 100;
        bar.Value = value;
        label.Text = text;
        Application.DoEvents();
    }
}
