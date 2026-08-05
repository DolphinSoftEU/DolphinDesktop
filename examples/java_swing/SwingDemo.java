// Java Swing headless-safe integration demo.
//
// A minimal Swing form covering every programmatic action supported
// through the Java Access Bridge:
//
//   * JButton      → .invoke()
//   * JCheckBox    → .toggle()
//   * JRadioButton → .select()
//   * JList        → .select()
//   * JTextField   → .set_value()
//   * JComboBox    → .expand() (drop-down)
//   * JLabel       → status assertion target
//
// Compile with:
//   javac SwingDemo.java
// Run:
//   java -Djava.accessibility=true SwingDemo
//
// The Java Access Bridge must be enabled system-wide (jabswitch /enable).

import javax.accessibility.AccessibleContext;
import javax.swing.*;
import javax.swing.border.EmptyBorder;
import java.awt.*;

public class SwingDemo extends JFrame {

    private final JTextField userField = new JTextField(20);
    private final JTextField passField = new JPasswordField(20);
    private final JCheckBox rememberChk = new JCheckBox("Remember me");
    private final JRadioButton standardRadio = new JRadioButton("Standard", true);
    private final JRadioButton premiumRadio = new JRadioButton("Premium");
    private final JComboBox<String> countryCombo = new JComboBox<>(
            new String[]{"Poland", "Germany", "France"});
    private final JList<String> hobbiesList = new JList<>(
            new String[]{"Reading", "Cycling", "Cooking"});
    private final JLabel statusLabel = new JLabel("Ready.");

    public SwingDemo() {
        super("Java Swing Demo");
        setDefaultCloseOperation(EXIT_ON_CLOSE);
        setSize(520, 460);
        setLayout(new BorderLayout());

        JPanel form = new JPanel(new GridBagLayout());
        form.setBorder(new EmptyBorder(12, 12, 12, 12));
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(4, 4, 4, 4);
        c.anchor = GridBagConstraints.WEST;

        int row = 0;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Username:"), c);
        c.gridx = 1;
        userField.setName("UserField");
        setAccName(userField, "UserField");
        form.add(userField, c);

        row++;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Password:"), c);
        c.gridx = 1;
        passField.setName("PassField");
        setAccName(passField, "PassField");
        form.add(passField, c);

        row++;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Options:"), c);
        c.gridx = 1;
        rememberChk.setName("RememberChk");
        setAccName(rememberChk, "Remember me");
        form.add(rememberChk, c);

        row++;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Tier:"), c);
        c.gridx = 1;
        JPanel radios = new JPanel(new FlowLayout(FlowLayout.LEFT, 8, 0));
        standardRadio.setName("StandardRadio");
        setAccName(standardRadio, "Standard");
        premiumRadio.setName("PremiumRadio");
        setAccName(premiumRadio, "Premium");
        ButtonGroup tierGroup = new ButtonGroup();
        tierGroup.add(standardRadio);
        tierGroup.add(premiumRadio);
        radios.add(standardRadio);
        radios.add(premiumRadio);
        form.add(radios, c);

        row++;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Country:"), c);
        c.gridx = 1;
        countryCombo.setName("CountryCombo");
        setAccName(countryCombo, "CountryCombo");
        form.add(countryCombo, c);

        row++;
        c.gridx = 0; c.gridy = row;
        form.add(new JLabel("Hobbies:"), c);
        c.gridx = 1;
        hobbiesList.setName("HobbiesList");
        setAccName(hobbiesList, "HobbiesList");
        form.add(new JScrollPane(hobbiesList), c);

        row++;
        c.gridx = 0; c.gridy = row; c.gridwidth = 2;
        c.anchor = GridBagConstraints.CENTER;
        JButton signInBtn = new JButton("Sign In");
        signInBtn.setName("SignInBtn");
        setAccName(signInBtn, "Sign In");
        signInBtn.addActionListener(e -> onSignIn());
        form.add(signInBtn, c);

        row++;
        c.gridx = 0; c.gridy = row; c.gridwidth = 2;
        JButton clearBtn = new JButton("Clear");
        clearBtn.setName("ClearBtn");
        setAccName(clearBtn, "Clear");
        clearBtn.addActionListener(e -> onClear());
        form.add(clearBtn, c);

        add(form, BorderLayout.CENTER);

        statusLabel.setName("StatusLabel");
        setAccName(statusLabel, "StatusLabel");
        statusLabel.setBorder(BorderFactory.createEtchedBorder());
        add(statusLabel, BorderLayout.SOUTH);
    }

    private void setAccName(JComponent c, String name) {
        AccessibleContext ac = c.getAccessibleContext();
        ac.setAccessibleName(name);
    }

    private void onSignIn() {
        String user = userField.getText();
        String pass = new String(((JPasswordField) passField).getPassword());
        boolean remember = rememberChk.isSelected();
        String tier = premiumRadio.isSelected() ? "Premium" : "Standard";
        Object country = countryCombo.getSelectedItem();
        Object hobby = hobbiesList.getSelectedValue();
        String status = String.format(
                "Signed in: user=%s pass=%s remember=%s tier=%s country=%s hobby=%s",
                user, pass.isEmpty() ? "-" : "***",
                remember, tier, country, hobby);
        setStatus(status);
    }

    private void onClear() {
        userField.setText("");
        passField.setText("");
        rememberChk.setSelected(false);
        standardRadio.setSelected(true);
        countryCombo.setSelectedIndex(0);
        hobbiesList.clearSelection();
        setStatus("Cleared.");
    }

    private void setStatus(String message) {
        SwingUtilities.invokeLater(() -> {
            statusLabel.setText(message);
            statusLabel.getAccessibleContext().setAccessibleDescription(message);
        });
    }

    public static void main(String[] args) {
        SwingUtilities.invokeLater(() -> new SwingDemo().setVisible(true));
    }
}
