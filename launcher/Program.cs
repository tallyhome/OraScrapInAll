using System;
using System.IO;
using System.Threading;
using System.Windows.Forms;

namespace OraScrap
{
    static class Program
    {
        [STAThread]
        static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            bool createdNew;
            using (var mutex = new Mutex(true, "Local\\OraScrapInAll", out createdNew))
            {
                if (!createdNew)
                {
                    if (MainForm.IsHealthy())
                        NativeMethods.BroadcastShow();
                    else
                        MessageBox.Show(
                            "OraScrapInAll est déjà en cours de démarrage.",
                            "OraScrapInAll",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information);
                    return;
                }

                Application.Run(new MainForm());
            }
        }

        internal static string Root()
        {
            return AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        }

        internal static void Log(string line)
        {
            try
            {
                string folder = Path.Combine(Root(), "data");
                Directory.CreateDirectory(folder);
                File.AppendAllText(
                    Path.Combine(folder, "launcher.log"),
                    DateTime.Now.ToString("HH:mm:ss ") + line + "\r\n");
            }
            catch { }
        }
    }

    static class NativeMethods
    {
        public const int WmShowOraScrap = 0x8001;

        public static void BroadcastShow()
        {
            // La seconde instance demande à la première de se montrer.
            PostMessage((IntPtr)0xffff, WmShowOraScrap, IntPtr.Zero, IntPtr.Zero);
        }

        [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
        static extern bool PostMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);
    }
}
