// Minimal Java Swing app that mimics Oracle Forms layout for integration
// testing of dolphin_desktop's Oracle Forms wrapper. Runs with
//   java -Djava.accessibility=true OracleFormsMock
// The Java Access Bridge must be enabled (jabswitch /enable). Compile with:
//   javac OracleFormsMock.java

import javax.accessibility.AccessibleContext;
import javax.swing.*;
import javax.swing.border.TitledBorder;
import java.awt.*;
import java.awt.event.KeyEvent;
import java.util.LinkedHashMap;
import java.util.Map;

public class OracleFormsMock extends JFrame {

    private final JLabel statusLine = new JLabel("Ready.");
    private final Map<String, JTextField> items = new LinkedHashMap<>();

    public OracleFormsMock() {
        super("Oracle Forms Mock — DEMO");
        setDefaultCloseOperation(EXIT_ON_CLOSE);
        setSize(720, 480);
        setLayout(new BorderLayout());

        setJMenuBar(buildMenuBar());

        JPanel main = new JPanel();
        main.setLayout(new BoxLayout(main, BoxLayout.Y_AXIS));
        main.add(buildBlock("EMPLOYEES", new String[]{"EMPNO", "ENAME", "SAL"}));
        main.add(Box.createVerticalStrut(12));
        main.add(buildBlock("DEPARTMENTS", new String[]{"DEPTNO", "DNAME"}));
        add(main, BorderLayout.CENTER);

        statusLine.setName("StatusLine");
        statusLine.setBorder(BorderFactory.createEtchedBorder());
        AccessibleContext ac = statusLine.getAccessibleContext();
        ac.setAccessibleName("StatusLine");
        ac.setAccessibleDescription("Ready.");
        add(statusLine, BorderLayout.SOUTH);

        // Install function-key bindings on the root pane so they fire
        // regardless of which item has focus.
        installFunctionKeys();

        // Focus the first item so tests see a deterministic starting point.
        SwingUtilities.invokeLater(() -> {
            if (!items.isEmpty()) items.values().iterator().next().requestFocusInWindow();
        });
    }

    // ---------------------------------------------------------------- //
    // Menu bar                                                          //
    // ---------------------------------------------------------------- //

    private JMenuBar buildMenuBar() {
        JMenuBar mb = new JMenuBar();

        JMenu action = new JMenu("Action");
        action.setMnemonic(KeyEvent.VK_A);
        action.add(item("New", () -> setStatus("New record entered.")));
        action.add(item("Save", () -> setStatus("Transaction complete.")));
        action.add(item("Exit", () -> dispose()));
        mb.add(action);

        JMenu query = new JMenu("Query");
        query.setMnemonic(KeyEvent.VK_Q);
        query.add(item("Enter", this::enterQuery));
        query.add(item("Execute", this::executeQuery));
        query.add(item("Cancel", this::cancelQuery));
        mb.add(query);

        JMenu record = new JMenu("Record");
        record.setMnemonic(KeyEvent.VK_R);
        record.add(item("First", () -> setStatus("Record 1 of 3")));
        record.add(item("Last", () -> setStatus("Record 3 of 3")));
        record.add(item("Next", () -> setStatus("Record 2 of 3")));
        record.add(item("Previous", () -> setStatus("Record 1 of 3")));
        mb.add(record);

        JMenu help = new JMenu("Help");
        help.setMnemonic(KeyEvent.VK_H);
        help.add(item("About", () -> JOptionPane.showMessageDialog(this,
                "Oracle Forms Mock v1.0")));
        mb.add(help);

        return mb;
    }

    private JMenuItem item(String label, Runnable action) {
        JMenuItem mi = new JMenuItem(label);
        mi.setName(label);
        mi.getAccessibleContext().setAccessibleName(label);
        mi.addActionListener(e -> action.run());
        return mi;
    }

    // ---------------------------------------------------------------- //
    // Blocks                                                            //
    // ---------------------------------------------------------------- //

    private JPanel buildBlock(String blockName, String[] itemNames) {
        JPanel panel = new JPanel(new GridLayout(0, 2, 8, 4));
        panel.setBorder(BorderFactory.createTitledBorder(
                BorderFactory.createEtchedBorder(),
                blockName,
                TitledBorder.LEFT, TitledBorder.TOP));
        panel.setName(blockName);
        panel.getAccessibleContext().setAccessibleName(blockName);

        for (String name : itemNames) {
            String fq = blockName + "." + name;
            JLabel lbl = new JLabel(name + ":");
            JTextField f = new JTextField(20);
            f.setName(fq);
            f.getAccessibleContext().setAccessibleName(fq);
            items.put(fq, f);
            panel.add(lbl);
            panel.add(f);
        }
        return panel;
    }

    // ---------------------------------------------------------------- //
    // Function keys                                                     //
    // ---------------------------------------------------------------- //

    // These bindings mirror dolphin's OracleFormsKey constants so the tests
    // can exercise the key-delivery path end to end. They are NOT an
    // independent authority on the real Forms runtime keymap — verify that
    // against your fmrweb.res, not against this mock.
    private void installFunctionKeys() {
        InputMap im = getRootPane().getInputMap(JComponent.WHEN_IN_FOCUSED_WINDOW);
        ActionMap am = getRootPane().getActionMap();

        bind(im, am, "F7", this::enterQuery);
        bind(im, am, "F8", this::executeQuery);
        bind(im, am, "F10", () -> setStatus("Transaction complete."));
        bind(im, am, "F9", this::openLov);
        bind(im, am, "control Q", this::cancelQuery);
        bind(im, am, "F1", () -> setStatus("Help topic: Oracle Forms Mock"));
    }

    private void bind(InputMap im, ActionMap am, String keyStroke, Runnable action) {
        im.put(KeyStroke.getKeyStroke(keyStroke), keyStroke);
        am.put(keyStroke, new AbstractAction() {
            public void actionPerformed(java.awt.event.ActionEvent e) { action.run(); }
        });
    }

    // ---------------------------------------------------------------- //
    // Oracle-Forms behavioural shortcuts                                //
    // ---------------------------------------------------------------- //

    private void enterQuery() { setStatus("Enter query."); }
    private void executeQuery() { setStatus("Record 1 of 3"); }
    private void cancelQuery() { setStatus("Query cancelled."); }

    private void openLov() {
        JDialog dlg = new JDialog(this, "List of Values", true);
        dlg.setSize(320, 240);
        dlg.setLocationRelativeTo(this);
        DefaultListModel<String> model = new DefaultListModel<>();
        for (String name : new String[]{"SMITH", "ALLEN", "WARD", "JONES", "MARTIN"}) {
            model.addElement(name);
        }
        JList<String> list = new JList<>(model);
        list.setName("LovList");
        list.getAccessibleContext().setAccessibleName("LovList");
        JScrollPane sp = new JScrollPane(list);
        JButton ok = new JButton("OK");
        ok.setName("OK");
        ok.getAccessibleContext().setAccessibleName("OK");
        ok.addActionListener(e -> {
            String v = list.getSelectedValue();
            if (v != null) {
                setStatus("LOV selected: " + v);
                // Also put it in the currently focused item if any.
                Component focused = FocusManager.getCurrentManager().getFocusOwner();
                if (focused instanceof JTextField) {
                    ((JTextField) focused).setText(v);
                }
            }
            dlg.dispose();
        });
        dlg.setLayout(new BorderLayout());
        dlg.add(sp, BorderLayout.CENTER);
        JPanel buttons = new JPanel();
        buttons.add(ok);
        dlg.add(buttons, BorderLayout.SOUTH);
        dlg.setVisible(true);
    }

    private void setStatus(String message) {
        SwingUtilities.invokeLater(() -> {
            statusLine.setText(message);
            // Keep accessibleName stable ("StatusLine") for locators —
            // push the changing text into accessibleDescription so a JAB
            // reader can pick it up without needing the visual buffer.
            statusLine.getAccessibleContext().setAccessibleDescription(message);
        });
    }

    // ---------------------------------------------------------------- //
    // Boot                                                              //
    // ---------------------------------------------------------------- //

    public static void main(String[] args) {
        // Force JAB-friendly UI (default L&F is Metal — fine, works with JAB).
        SwingUtilities.invokeLater(() -> new OracleFormsMock().setVisible(true));
    }
}
