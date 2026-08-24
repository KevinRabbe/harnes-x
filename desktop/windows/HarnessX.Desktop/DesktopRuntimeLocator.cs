namespace HarnessX.Desktop;

internal static class DesktopRuntimeLocator
{
    private const string AppServerName = "harness-x-app-server.exe";

    public static string ResolveOrPrompt(IWin32Window owner, DesktopPaths paths)
    {
        var resolved = TryResolve(paths);
        if (resolved is not null)
        {
            Persist(paths, resolved);
            return resolved;
        }

        using var dialog = new OpenFileDialog
        {
            Title = "Locate the Harness X App Server",
            Filter = "Harness X App Server (harness-x-app-server.exe)|harness-x-app-server.exe|Programs (*.exe)|*.exe",
            CheckFileExists = true,
            CheckPathExists = true,
            Multiselect = false,
            RestoreDirectory = true,
        };
        if (dialog.ShowDialog(owner) != DialogResult.OK)
        {
            throw new OperationCanceledException(
                "Harness X needs the existing harness-x-app-server.exe from your Python environment. "
                + "Select it once and Harness X will remember the location.");
        }

        var selected = Path.GetFullPath(dialog.FileName);
        if (!string.Equals(Path.GetFileName(selected), AppServerName, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException(
                "Select harness-x-app-server.exe from the Python environment where Harness X is installed.");
        }
        Persist(paths, selected);
        return selected;
    }

    internal static string? TryResolve(DesktopPaths paths)
    {
        var configured = ExistingFile(Environment.GetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE"));
        if (configured is not null)
        {
            return configured;
        }

        var remembered = ReadRemembered(paths.AppServerExecutablePathFile);
        if (remembered is not null)
        {
            return remembered;
        }

        var adjacent = ExistingFile(Path.Combine(AppContext.BaseDirectory, AppServerName));
        if (adjacent is not null)
        {
            return adjacent;
        }

        foreach (var root in CandidateRoots())
        {
            foreach (var environmentName in new[] { ".venv", "venv" })
            {
                var candidate = ExistingFile(
                    Path.Combine(root, environmentName, "Scripts", AppServerName));
                if (candidate is not null)
                {
                    return candidate;
                }
            }
        }

        var pathValue = Environment.GetEnvironmentVariable("PATH") ?? string.Empty;
        foreach (var directory in pathValue.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var candidate = ExistingFile(Path.Combine(directory.Trim(), AppServerName));
            if (candidate is not null)
            {
                return candidate;
            }
        }
        return null;
    }

    private static IEnumerable<string> CandidateRoots()
    {
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var start in new[] { Environment.CurrentDirectory, AppContext.BaseDirectory })
        {
            DirectoryInfo? current;
            try
            {
                current = new DirectoryInfo(Path.GetFullPath(start));
            }
            catch (Exception exc) when (exc is ArgumentException or NotSupportedException or PathTooLongException)
            {
                continue;
            }

            for (var depth = 0; current is not null && depth < 12; depth++, current = current.Parent)
            {
                if (seen.Add(current.FullName))
                {
                    yield return current.FullName;
                }
            }
        }
    }

    private static string? ReadRemembered(string path)
    {
        try
        {
            if (!File.Exists(path))
            {
                return null;
            }
            return ExistingFile(File.ReadAllText(path).Trim());
        }
        catch (OSError)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static string? ExistingFile(string? path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }
        try
        {
            var full = Path.GetFullPath(path.Trim());
            return File.Exists(full) ? full : null;
        }
        catch (Exception exc) when (exc is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return null;
        }
    }

    private static void Persist(DesktopPaths paths, string executable)
    {
        var temporary = paths.AppServerExecutablePathFile + ".tmp";
        File.WriteAllText(temporary, executable + Environment.NewLine);
        File.Move(temporary, paths.AppServerExecutablePathFile, overwrite: true);
    }
}
