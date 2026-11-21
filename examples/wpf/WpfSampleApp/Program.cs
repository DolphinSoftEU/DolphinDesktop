using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;

namespace WpfSampleApp;

internal static class Program
{
    [STAThread]
    public static void Main()
    {
        var app = new Application { ShutdownMode = ShutdownMode.OnLastWindowClose };
        CustomerWindow().Show();
        ProductWindow().Show();
        FileBrowserWindow().Show();
        app.Run();
    }

    private static T Identify<T>(T control, string id) where T : FrameworkElement
    {
        AutomationProperties.SetAutomationId(control, id);
        return control;
    }

    private static Window Window(string title, UIElement content) => new()
    {
        Title = title,
        Width = 440,
        Height = 360,
        Content = content
    };

    private static Window CustomerWindow()
    {
        var panel = new StackPanel { Margin = new Thickness(16) };
        var first = Identify(new TextBox(), "txtFirstName");
        var last = Identify(new TextBox(), "txtLastName");
        var email = Identify(new TextBox(), "txtEmail");
        var status = Identify(new ComboBox(), "cmbStatus");
        status.Items.Add("Active");
        status.Items.Add("Inactive");
        var newsletter = Identify(new CheckBox { Content = "Newsletter" }, "chkNewsletter");
        var save = Identify(new Button { Content = "Save" }, "btnSave");
        var result = Identify(new TextBlock(), "lblStatus");
        foreach (var field in new[] { ("First name", first), ("Last name", last), ("Email", email) })
        {
            panel.Children.Add(new TextBlock { Text = field.Item1 });
            panel.Children.Add(field.Item2);
        }
        panel.Children.Add(status);
        panel.Children.Add(newsletter);
        panel.Children.Add(save);
        panel.Children.Add(result);
        save.Click += (_, _) =>
        {
            result.Text = string.IsNullOrWhiteSpace(first.Text) ||
                string.IsNullOrWhiteSpace(last.Text) || !email.Text.Contains('@') ||
                status.SelectedItem is null
                ? "Complete all fields"
                : $"Saved: {first.Text} {last.Text}, {status.SelectedItem}, newsletter={newsletter.IsChecked}";
        };
        return Window("Customer Form", panel);
    }

    private static Window ProductWindow()
    {
        var panel = new StackPanel { Margin = new Thickness(16) };
        var products = new ListBox();
        foreach (var name in new[] { "Notebook", "Pencil", "Paper" })
            products.Items.Add(name);
        var selected = Identify(new TextBlock(), "lblSelected");
        products.SelectionChanged += (_, _) => selected.Text = products.SelectedItem?.ToString() ?? "";
        panel.Children.Add(products);
        panel.Children.Add(selected);
        return Window("Product List", panel);
    }

    private static Window FileBrowserWindow()
    {
        var panel = new StackPanel { Margin = new Thickness(16) };
        var tree = new TreeView();
        var documents = new TreeViewItem { Header = "Documents" };
        var reports = new TreeViewItem { Header = "Reports" };
        documents.Items.Add(reports);
        tree.Items.Add(documents);
        var current = Identify(new TextBlock(), "lblCurrentPath");
        reports.Selected += (_, args) =>
        {
            current.Text = @"Documents\Reports";
            args.Handled = true;
        };
        panel.Children.Add(tree);
        panel.Children.Add(current);
        return Window("File Browser", panel);
    }
}
