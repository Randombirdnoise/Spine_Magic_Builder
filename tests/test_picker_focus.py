import unittest
from unittest.mock import Mock, patch

import picker_focus
import spine_candidate_picker_gui as picker


class FocusHandbackTests(unittest.TestCase):
    def test_late_viewer_foreground_is_returned_to_picker(self):
        root = Mock()
        root.winfo_id.return_value = 100
        with patch.object(picker_focus, "foreground_owner", side_effect=[200, 100]), \
             patch.object(picker_focus, "bring_to_foreground") as bring:
            picker_focus.hand_back_after_launch(root, 200, True)
            root.after.call_args_list[0].args[1]()
            root.after.call_args_list[1].args[1]()
            self.assertEqual(bring.call_count, 1)
            self.assertEqual(bring.call_args.args[0], root)

    def test_unrelated_foreground_is_left_alone(self):
        root = Mock()
        root.winfo_id.return_value = 100
        with patch.object(picker_focus, "foreground_owner", side_effect=[999]), \
             patch.object(picker_focus, "bring_to_foreground") as bring:
            picker_focus.hand_back_after_launch(root, 200, True)
            root.after.call_args_list[0].args[1]()
            bring.assert_not_called()

    def test_launch_outside_picker_does_not_reclaim_focus(self):
        root = Mock()
        picker_focus.hand_back_after_launch(root, 200, False)
        root.after.assert_not_called()

    def test_picker_passes_viewer_pid_and_candidate_widget_to_handback(self):
        app = object.__new__(picker.CandidatePickerApp)
        app.root = Mock()
        app.cand_tree = Mock()
        app.viewer = Mock()
        app.viewer.exists.return_value = True
        app.restart_viewer = Mock()
        app.restart_viewer.get.return_value = False
        app.viewer_proc = None
        app.log_line = Mock()
        app.current_set_page = Mock(return_value=(Mock(folder=Mock(), skeletons=[], atlas=None), Mock()))
        proc = Mock(pid=345)
        with patch.object(picker, "foreground_owner", return_value=picker.os.getpid()), \
             patch.object(picker.subprocess, "Popen", return_value=proc), \
             patch.object(picker, "hand_back_after_launch") as handback:
            app.launch_viewer()
        handback.assert_called_once_with(app.root, 345, True, app.cand_tree)


if __name__ == "__main__":
    unittest.main()
