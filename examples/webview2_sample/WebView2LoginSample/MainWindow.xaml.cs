using System.IO;
using System.Windows;
using Microsoft.Web.WebView2.Core;

namespace WebView2LoginSample;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private async void OnLoaded(object sender, RoutedEventArgs e)
    {
        await WebView.EnsureCoreWebView2Async();
    }

    private void WebView_Initialized(object sender, CoreWebView2InitializationCompletedEventArgs e)
    {
        var htmlPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "login.html");
        WebView.Source = new Uri(htmlPath);

        // JS → Native: update StatusBar with login result
        WebView.CoreWebView2.WebMessageReceived += (_, args) =>
        {
            StatusBar.Text = $"Status: {args.TryGetWebMessageAsString()}";
        };
    }
}
