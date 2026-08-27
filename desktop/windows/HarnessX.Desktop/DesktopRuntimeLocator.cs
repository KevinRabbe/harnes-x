namespace HarnessX.Desktop;

internal static class DesktopRuntimeLocator
{
    private const string AppServerName = "harness-x-app-server.exe";

    public static string ResolveOrPrompt(IWin32Window owner, DesktopPaths paths)
    {
        var resolved = TryResolve(paths);
        if (resolved is not null)
        {
            TryRemember(paths, resolved);
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
                "Harness X needs the existing harness-x-app-server.exe from your Python environment.");
        }

        var selected = ExistingAppServerFile(dialog.FileName);
        if (selected is null)
        {
            throw new InvalidOperationException(
                "Select harness-x-app-server.exe from the Python environment where Harness X is installed.");
        }
        TryRemember(paths, selected);
        return selected;
    }

    internal static string? TryResolve(DesktopPaths paths)
    {
        // Explicit operator configuration is authoritative and may intentionally use a wrapper name.
        var configured = ExistingFile(Environment.GetEnvironmentVariable("HARNESS_X_APP_SERVER_EXECUTABLE"));
        if (configured is not null)
        {
            return configured;
        }

        var adjacent = ExistingAppServerFile(Path.Combine(AppContext.BaseDirectory, AppServerName));
        if (adjacent is not null)
        {
            return adjacent;
        }

        var remembered = ReadRemembered(paths.AppServerExecutablePathFile);
        if (remembered is not null)
        {
            return remembered;
        }

        foreach (var root in CandidateRoots())
        {
            foreach (var environmentName in new[] { ".venv", "venv" })
            {
                var candidate = ExistingAppServerFile(
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
            var normalizedDirectory = directory.Trim().Trim('"');
            var candidate = ExistingAppServerFile(Path.Combine(normalizedDirectory, AppServerName));
            if (candidate is not null)
            {
                return candidate;
            }
        }
        return null;
    }

    internal static bool TryRemember(DesktopPaths paths, string executable)
    {
        var normalized = ExistingAppServerFile(executable);
        if (normalized is null)
        {
            return false;
        }

        var temporary = paths.AppServerExecutablePathFile + ".tmp";
        try
        {
            File.WriteAllText(temporary, normalized + Environment.NewLine);
            File.Move(temporary, paths.AppServerExecutablePathFile, overwrite: true);
            return true;
        }
        catch (IOException)
        {
            DeleteTemporaryBestEffort(temporary);
            return false;
        }
        catch (UnauthorizedAccessException)
        {
            DeleteTemporaryBestEffort(temporary);
            return false;
        }
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
            return ExistingAppServerFile(File.ReadAllText(path).Trim());
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static string? ExistingAppServerFile(string? path)
    {
        var existing = ExistingFile(path);
        if (existing is null
            || !string.Equals(Path.GetFileName(existing), AppServerName, StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }
        return existing;
    }

    private static string? ExistingFile(string? path)
    {
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }
        try
        {
            var full = Path.GetFullPath(path.Trim().Trim('"'));
            return File.Exists(full) ? full : null;
        }
        catch (Exception exc) when (exc is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return null;
        }
    }

    private static void DeleteTemporaryBestEffort(string path)
    {
        try
        {
            File.Delete(path);
        }
        catch
        {
        }
    }
}
