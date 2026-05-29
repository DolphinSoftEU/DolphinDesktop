using System;
using System.IO;
using System.Windows;
using System.Windows.Navigation;

namespace IESampleApp;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        // Load an external HTML file if present next to the exe,
        // otherwise fall back to the inline form.
        var htmlPath = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "form.html");
        if (File.Exists(htmlPath))
            WebBrowser.Navigate(new Uri(htmlPath));
        else
            WebBrowser.NavigateToString(GetInlineHtml());
    }

    private void WebBrowser_LoadCompleted(object sender, NavigationEventArgs e)
    {
        UpdateStatus("Status: loaded");
    }

    private void NavBtn_Click(object sender, RoutedEventArgs e)
    {
        WebBrowser.Refresh();
        UpdateStatus("Status: refreshed");
    }

    private void UpdateStatus(string text)
    {
        StatusBar.Text = text;
        // Keep AutomationProperties.Name in sync so UIA readers see the update.
        System.Windows.Automation.AutomationProperties.SetName(StatusBar, text);
    }

    private static string GetInlineHtml() => """
        <!DOCTYPE html>
        <html>
        <head>
          <meta charset="UTF-8">
          <title>IE/Trident Form - Dolphin sample</title>
          <style>
            body { font-family: Segoe UI, sans-serif; padding: 24px; }
            label { display: block; margin-top: 12px; font-weight: bold; }
            input[type=text] { width: 300px; padding: 4px; margin-top: 4px; }
            button { margin-top: 16px; padding: 6px 20px; font-size: 14px; }
            #result { margin-top: 16px; color: #1a7a1a; font-weight: bold; }
          </style>
        </head>
        <body>
          <h2>Dolphin IE/Trident Sample Form</h2>
          <label for="nameInput">Name</label>
          <input id="nameInput" type="text" aria-label="Name" placeholder="Enter name" />
          <label for="cityInput">City</label>
          <input id="cityInput" type="text" aria-label="City" placeholder="Enter city" />
          <br/>
          <button id="submitBtn"
                  onclick="submit()">Submit</button>
          <p id="result"></p>
          <script>
            function submit() {
              var name = document.getElementById('nameInput').value;
              var city = document.getElementById('cityInput').value;
              document.getElementById('result').innerText =
                'Submitted: ' + name + ' from ' + city;
            }
          </script>
        </body>
        </html>
        """;
}
