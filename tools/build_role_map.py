#!/usr/bin/env python3
"""build_role_map — generate the Android <-> ArkUI role map from authoritative inventories.

Sources (fetched 2026-11-23):
  hmos  : openharmony/docs master, zh-cn/application-dev/reference/apis-arkui/arkui-ts
          51 basic components + 45 containers
  android: android-36 android.jar -> android/widget (98 classes)
          material3 runtime jar (the version sunflower compiles against)
  compose: androidx.compose.material3 component files

Every component of both sides must end up either in a role or in the
`no_counterpart` list, otherwise this script prints it as UNMAPPED and exits 1.
That coverage assertion is the point: nothing can be silently forgotten.

Outputs: tools/role_map.yaml, docs/role-map.md
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("needs pyyaml: uv run --with pyyaml python tools/build_role_map.py")
    raise SystemExit(2)

# ── HarmonyOS inventory: ArkUI ArkTS declarative components ──────────────────
ARKUI_BASIC = [
    "ArcScrollBar", "Blank", "Button", "CalendarPicker", "Checkbox", "CheckboxGroup",
    "Component3D", "ContainerSpan", "DataPanel", "DatePicker", "DepthComponent", "Divider",
    "Gauge", "Image", "ImageAnimator", "ImageSpan", "LoadingProgress", "Marquee", "Menu",
    "MenuItem", "MenuItemGroup", "NavDestination", "Navigation", "NavRouter", "NodeContainer",
    "PatternLock", "Progress", "QRCode", "Radio", "Rating", "RichEditor", "RichText",
    "ScrollBar", "Search", "Select", "Slider", "Span", "Stepper", "StepperItem",
    "SymbolGlyph", "SymbolSpan", "Text", "TextArea", "TextClock", "TextInput", "TextPicker",
    "TextTimer", "TimePicker", "Toggle", "ToolbarItem", "XComponent",
]
ARKUI_CONTAINER = [
    "AlphabetIndexer", "ArcAlphabetIndexer", "ArcList", "ArcListItem", "ArcSwiper", "Badge",
    "Column", "ColumnSplit", "ContainerReader", "Counter", "DynamicLayout", "EmbeddedComponent",
    "Flex", "FlowItem", "FolderStack", "FormLink", "Grid", "GridCol", "GridContainer",
    "GridItem", "GridRow", "Hyperlink", "LazyColumnLayout", "LazyVGridLayout",
    "LazyVWaterFlowLayout", "List", "ListItem", "ListItemGroup", "Navigator", "Panel", "Refresh",
    "RelativeContainer", "Row", "RowSplit", "Scroll", "SideBarContainer",
    "Stack", "Swiper", "TabContent", "Tabs", "WaterFlow",
]

ARKUI_MEDIA = ["Video"]
# Web/Canvas/SVG references live under apis-web / apis-arkui graphics, not arkui-ts
ARKUI_EXTRA = ["Web"]
ARKUI_ADVANCED = [  # ohos-arkui-advanced-* (high-level composite components)
    "ArcButton", "ArcSlider", "Chip", "ChipGroup", "ComposeListItem", "ComposeTitleBar",
    "Counter", "CounterV2", "Dialog", "DialogV2", "DownloadFileButton", "EditableTitleBar",
    "ExceptionPrompt", "Filter", "FoldSplitContainer", "FullScreenLaunchComponent",
    "GridObjectSortComponent", "MultiNavigation", "Popup", "PopupV2", "ProgressButton",
    "ProgressButtonV2", "SegmentButton", "SegmentButtonV2", "SelectTitleBar", "SelectionMenu",
    "SplitLayout", "SubHeader", "SubHeaderV2", "SwipeRefresher", "TabTitleBar", "ToolBar",
    "ToolBarV2", "TreeView", "formmenu",
]
ARKUI_ATOMIC = [  # ohos-atomicservice-*
    "AtomicServiceNavigation", "AtomicServiceSearch", "AtomicServiceTabs", "AtomicServiceWeb",
    "HalfScreenLaunchComponent", "InterstitialDialogAction", "NavPushPathHelper",
]

# ── android inventory ───────────────────────────────────────────────────────
ANDROID_WIDGET = [
    "AbsListView", "AbsoluteLayout", "AbsSeekBar", "AbsSpinner", "ActionMenuView", "Adapter",
    "AdapterView", "AdapterViewAnimator", "AdapterViewFlipper", "Advanceable", "AlphabetIndexer",
    "AnalogClock", "ArrayAdapter", "AutoCompleteTextView", "BaseAdapter", "BaseExpandableListAdapter",
    "Button", "CalendarView", "Checkable", "CheckBox", "CheckedTextView", "Chronometer",
    "CompoundButton", "CursorAdapter", "CursorTreeAdapter", "DatePicker", "DialerFilter",
    "DigitalClock", "EdgeEffect", "EditText", "ExpandableListAdapter", "ExpandableListView",
    "Filter", "Filterable", "FilterQueryProvider", "FrameLayout", "Gallery", "GridLayout",
    "GridView", "HeaderViewListAdapter", "HeterogeneousExpandableList", "HorizontalScrollView",
    "ImageButton", "ImageSwitcher", "ImageView", "LinearLayout", "ListAdapter", "ListPopupWindow",
    "ListView", "Magnifier", "MediaController", "MultiAutoCompleteTextView", "NumberPicker",
    "OverScroller", "PopupMenu", "PopupWindow", "ProgressBar", "QuickContactBadge", "RadioButton",
    "RadioGroup", "RatingBar", "RelativeLayout", "RemoteViews", "RemoteViewsService",
    "ResourceCursorAdapter", "ResourceCursorTreeAdapter", "Scroller", "ScrollView", "SearchView",
    "SectionIndexer", "SeekBar", "ShareActionProvider", "SimpleAdapter", "SimpleCursorAdapter",
    "SimpleCursorTreeAdapter", "SimpleExpandableListAdapter", "SlidingDrawer", "Space", "Spinner",
    "SpinnerAdapter", "StackView", "Switch", "TabHost", "TableLayout", "TableRow", "TabWidget",
    "TextClock", "TextSwitcher", "TextView", "ThemedSpinnerAdapter", "TimePicker", "Toast",
    "ToggleButton", "Toolbar", "TwoLineListItem", "VideoView", "ViewAnimator", "ViewFlipper",
    "ViewSwitcher", "WrapperListAdapter", "ZoomButton", "ZoomButtonsController", "ZoomControls",
]
ANDROID_MATERIAL = [  # com.google.android.material.*
    "MaterialButton", "MaterialSwitch", "SwitchMaterial", "Chip", "ChipGroup",
    "TextInputLayout", "TextInputEditText", "TabLayout", "BottomNavigationView",
    "NavigationView", "FloatingActionButton", "ExtendedFloatingActionButton",
    "CardView", "MaterialCardView", "AppBarLayout", "CollapsingToolbarLayout",
    "BottomSheetDialog", "BottomSheetDialogFragment", "MaterialAlertDialogBuilder",
    "Slider", "RangeSlider", "Badge", "BadgeDrawable", "Snackbar", "SwipeRefreshLayout",
    "MaterialToolbar", "CircularProgressIndicator", "LinearProgressIndicator",
]
ANDROID_EXTRA = [  # outside android.widget / androidx.compose.material3
    "WebView", "SurfaceView", "TextureView", "GLSurfaceView",      # android.webkit / android.view
    "Dialog", "DialogFragment", "MaterialDatePicker", "DatePickerDialog", "TimePickerDialog",
    "BottomSheetDialogFragment", "PopupWindow",
]
ANDROID_ANDROIDX = [  # other androidx widgets the migration actually meets
    "RecyclerView", "ViewPager", "ViewPager2", "SwipeRefreshLayout", "LazyVerticalGrid",
    "LazyColumn", "LazyRow", "BottomNavigationView", "TextSwitcher", "ViewSwitcher",
    "Checkbox", "Image",
]
ANDROID_COMPOSE = [  # androidx.compose.material3.*
    "AlertDialog", "AppBar", "Badge", "BottomSheetScaffold", "Button", "Card", "Checkbox", "Chip",
    "DatePicker", "DateRangePicker", "Divider", "ElevatedButton", "FilledTonalButton",
    "FilterChip", "FloatingActionButton", "Icon", "IconButton", "IconToggleButton", "Label",
    "LinearProgressIndicator", "CircularProgressIndicator", "ListItem", "Menu", "NavigationBar",
    "NavigationDrawer", "NavigationRail", "OutlinedButton", "OutlinedTextField", "RadioButton",
    "Scaffold", "SearchBar", "SegmentedButton", "Slider", "RangeSlider", "Snackbar", "Surface",
    "SwipeToDismissBox", "Switch", "Tab", "TabRow", "Text", "TextButton", "TextField",
    "TimePicker", "Tooltip", "TopAppBar", "CenterAlignedTopAppBar", "AssistChip", "InputChip",
    "SuggestionChip", "ModalBottomSheet", "DropdownMenu", "ExposedDropdownMenuBox",
    "PullToRefreshBox", "HorizontalDivider", "VerticalDivider",
]

# ── role table ──────────────────────────────────────────────────────────────
# check_style=False -> D1: the platform owns the look, compare existence/position/state only
# check_style=True  -> D2: appearance is ours, compare it
# state: attribute that carries the widget state on each side ("" = none)
ROLES: dict[str, dict] = {
    # ---- platform-owned (D1) ----
    "toggle":       {"check_style": False, "state": "checked",
                     "android": ["Switch", "SwitchMaterial", "MaterialSwitch", "ToggleButton",
                                 "CompoundButton"],
                     "hmos": ["Toggle"]},
    "checkbox":     {"check_style": False, "state": "checked",
                     "android": ["CheckBox", "Checkbox"],
                     "hmos": ["Checkbox"]},
    "checkbox_group": {"check_style": False, "state": "checked", "android_via": "handrolled",
                     # Android has no group widget: each CheckBox manages itself, grouping lives in app code
                     "android": [],
                     "hmos": ["CheckboxGroup"]},
    "radio":        {"check_style": False, "state": "checked",
                     "android": ["RadioButton", "RadioGroup"],
                     "hmos": ["Radio"]},
    "slider":       {"check_style": False, "state": "value",
                     "android": ["SeekBar", "AbsSeekBar", "Slider", "RangeSlider"],
                     "hmos": ["Slider", "ArcSlider"]},
    "progress":     {"check_style": False, "state": "value",
                     "android": ["ProgressBar", "CircularProgressIndicator",
                                 "LinearProgressIndicator"],
                     "hmos": ["Progress", "LoadingProgress", "Gauge"]},
    "rating":       {"check_style": False, "state": "value",
                     "android": ["RatingBar"],
                     "hmos": ["Rating"]},
    "input":        {"check_style": False, "state": "text",
                     "android": ["EditText", "TextInputEditText", "TextInputLayout",
                                 "AutoCompleteTextView", "MultiAutoCompleteTextView",
                                 "TextField", "OutlinedTextField"],
                     "hmos": ["TextInput", "TextArea", "RichEditor"]},
    "search":       {"check_style": False, "state": "text",
                     "android": ["SearchView", "SearchBar"],
                     "hmos": ["Search", "AtomicServiceSearch"]},
    "picker_date":  {"check_style": False, "state": "text",
                     "android": ["DatePicker", "CalendarView", "DateRangePicker"],
                     "hmos": ["DatePicker", "CalendarPicker"]},
    "picker_time":  {"check_style": False, "state": "text",
                     "android": ["TimePicker"],
                     "hmos": ["TimePicker"]},
    "picker_text":  {"check_style": False, "state": "text",
                     "android": ["Spinner", "NumberPicker", "AbsSpinner"],
                     "hmos": ["TextPicker", "Select"]},
    "stepper":      {"check_style": False, "state": "text", "android_via": "handrolled",
                     # Android has no stepper widget: ZoomButton is a deprecated zoom control, not equivalent
                     "android": [],
                     "hmos": ["Stepper", "StepperItem", "Counter", "CounterV2"]},
    "tabs":         {"check_style": False, "state": "selected",
                     "android": ["TabHost", "TabWidget", "TabLayout", "TabRow", "Tab"],
                     "hmos": ["Tabs", "TabContent", "AtomicServiceTabs"]},
    "appbar":       {"check_style": False, "state": "",
                     "android": ["Toolbar", "MaterialToolbar", "ActionMenuView", "AppBarLayout",
                                 "CollapsingToolbarLayout", "TopAppBar", "CenterAlignedTopAppBar",
                                 "AppBar"],
                     "hmos": ["Navigation", "ToolbarItem", "ComposeTitleBar",
                              "SelectTitleBar", "EditableTitleBar", "TabTitleBar", "ToolBar",
                              "ToolBarV2", "SubHeader", "SubHeaderV2"]},
    "navbar":       {"check_style": False, "state": "selected",
                     "android": ["BottomNavigationView", "NavigationView", "NavigationBar",
                                 "NavigationRail", "NavigationDrawer"],
                     "hmos": ["Navigator", "NavRouter", "SideBarContainer", "MultiNavigation",
                              "AtomicServiceNavigation"]},
    "menu":         {"check_style": False, "state": "selected",
                     "android": ["PopupMenu", "ListPopupWindow", "DropdownMenu",
                                 "ExposedDropdownMenuBox", "Menu"],
                     "hmos": ["Menu", "MenuItem", "MenuItemGroup", "SelectionMenu",
                              "formmenu", "Popup", "PopupV2"]},
    "filter":       {"check_style": False, "state": "", "android_via": "handrolled",
                     # Android has no filter component: bottom sheet + checkboxes, hand-built
                     "android": [],
                     "hmos": ["Filter"]},
    "dialog":       {"check_style": False, "state": "",
                     "android": ["BottomSheetDialog", "BottomSheetDialogFragment",
                                 "MaterialAlertDialogBuilder", "AlertDialog", "BottomSheetScaffold",
                                 "ModalBottomSheet", "Dialog", "DialogFragment",
                                 "MaterialDatePicker", "DatePickerDialog", "TimePickerDialog",
                                 "PopupWindow"],
                     "hmos": ["Panel", "Dialog", "DialogV2", "ExceptionPrompt",
                              "InterstitialDialogAction"]},
    "toast":        {"check_style": False, "state": "", "hmos_via": "api",
                     # promptAction.showToast() — an API, not a component; never a node in a dump
                     "android": ["Toast", "Snackbar"],
                     "hmos": []},
    "refresh":      {"check_style": False, "state": "",
                     "android": ["SwipeRefreshLayout", "PullToRefreshBox"],
                     "hmos": ["Refresh", "SwipeRefresher"]},
    "badge":        {"check_style": False, "state": "text",
                     "android": ["Badge", "BadgeDrawable"],
                     "hmos": ["Badge"]},
    "divider":      {"check_style": False, "state": "",
                     "android": ["Divider", "HorizontalDivider", "VerticalDivider"],
                     "hmos": ["Divider"]},
    "scrollbar":    {"check_style": False, "state": "", "android_via": "attribute",
                     # Android scrollbars are a View attribute (android:scrollbars), no widget class exists
                     "android": [],
                     "hmos": ["ScrollBar", "ArcScrollBar"]},
    "swipe":        {"check_style": False, "state": "", "hmos_via": "attribute",
                     # ListItem().swipeAction({start/end}) — an attribute, not a standalone component
                     "android": ["SwipeToDismissBox"],
                     "hmos": []},

    # ---- content / app-authored (D2) ----
    "button":       {"check_style": True, "state": "text",
                     "android": ["Button", "MaterialButton", "OutlinedButton", "TextButton",
                                 "ElevatedButton", "FilledTonalButton"],
                     "hmos": ["Button", "ArcButton", "ProgressButton", "ProgressButtonV2",
                              "DownloadFileButton"]},
    "fab":          {"check_style": True, "state": "", "hmos_via": "handrolled",
                     # no FAB component: Button({type: ButtonType.Circle}) + position

                     "android": ["FloatingActionButton", "ExtendedFloatingActionButton"],
                     "hmos": []},
    "icon_button":  {"check_style": True, "state": "",
                     "android": ["ImageButton", "IconButton", "IconToggleButton", "Icon"],
                     "hmos": ["SymbolGlyph"]},
    "chip":         {"check_style": True, "state": "text",
                     "android": ["Chip", "FilterChip", "AssistChip", "InputChip", "SuggestionChip"],
                     "hmos": ["Chip"]},
    "chip_group":   {"check_style": True, "state": "selected",
                     "android": ["ChipGroup"],
                     "hmos": ["ChipGroup"]},
    "segmented":    {"check_style": False, "state": "selected",
                     "android": ["SegmentedButton"],
                     "hmos": ["SegmentButton", "SegmentButtonV2"]},
    "list_item":    {"check_style": True, "state": "",
                     "android": ["ListItem", "CheckedTextView"],
                     "hmos": ["ComposeListItem", "ListItem"]},
    "label":        {"check_style": True, "state": "text",
                     "android": ["TextView", "Text", "TwoLineListItem"],
                     "hmos": ["Text", "Span", "ContainerSpan", "SymbolSpan"]},
    "image":        {"check_style": True, "state": "",
                     "android": ["ImageView", "QuickContactBadge", "Image", "Magnifier"],
                     "hmos": ["Image", "ImageSpan", "ImageAnimator"]},
    "video":        {"check_style": True, "state": "",
                     "android": ["VideoView", "MediaController", "SurfaceView", "TextureView",
                                 "GLSurfaceView"],
                     "hmos": ["Video", "XComponent"]},
    "web":          {"check_style": True, "state": "",
                     "android": ["WebView"],
                     "hmos": ["Web", "RichText", "AtomicServiceWeb"]},
    "card":         {"check_style": True, "state": "", "hmos_via": "handrolled",
                     # no Card component: Column/Row + borderRadius + shadow, hand-built

                     "android": ["CardView", "MaterialCardView", "Card", "Surface", "Scaffold"],
                     "hmos": []},
    "list":         {"check_style": True, "state": "",
                     "android": ["ListView", "AbsListView", "RecyclerView", "LazyColumn",
                                 "LazyRow"],
                     "hmos": ["List", "ListItemGroup"]},
    "grid":         {"check_style": True, "state": "",
                     "android": ["GridView", "GridLayout", "LazyVerticalGrid"],
                     "hmos": ["Grid", "GridItem", "GridRow", "GridCol", "WaterFlow", "FlowItem",
                              "LazyVGridLayout", "LazyVWaterFlowLayout", "GridObjectSortComponent"]},
    "pager":        {"check_style": True, "state": "selected",
                     "android": ["ViewPager", "ViewPager2", "AdapterViewAnimator",
                                 "AdapterViewFlipper", "ViewFlipper", "StackView"],
                     "hmos": ["Swiper", "ArcSwiper"]},
    "expandable":   {"check_style": True, "state": "",
                     # maps to @ohos.arkui.advanced.TreeView

                     "android": ["ExpandableListView"],
                     "hmos": ["TreeView"]},
    "tooltip":      {"check_style": True, "state": "", "hmos_via": "attribute",
                     # .bindTips() — a universal attribute, not a component
                     "android": ["Tooltip"],
                     "hmos": []},
    "clock":        {"check_style": True, "state": "text",
                     "android": ["AnalogClock", "TextClock", "DigitalClock", "Chronometer"],
                     "hmos": ["TextClock", "TextTimer"]},
    "switcher":     {"check_style": True, "state": "", "hmos_via": "handrolled",
                     # no ViewSwitcher: Stack + animateTo/transition

                     "android": ["TextSwitcher", "ViewSwitcher", "ImageSwitcher"],
                     "hmos": []},
    "container":    {"check_style": False, "state": "",
                     "android": ["FrameLayout", "LinearLayout", "RelativeLayout", "AbsoluteLayout",
                                 "TableLayout", "TableRow", "Space", "ScrollView",
                                 "HorizontalScrollView", "RemoteViews", "ViewAnimator",
                                 "AlphabetIndexer"],
                     "hmos": ["Column", "Row", "ColumnSplit", "RowSplit", "Stack", "Flex", "Blank",
                              "RelativeContainer", "Scroll", "DynamicLayout", "FolderStack",
                              "NodeContainer", "EmbeddedComponent",
                              "ContainerReader", "FormLink", "Hyperlink",
                              "GridContainer", "LazyColumnLayout", "AlphabetIndexer",
                              "NavDestination",
                              "FoldSplitContainer", "SplitLayout", "FullScreenLaunchComponent",
                              "HalfScreenLaunchComponent"]},
}

# things that genuinely have no counterpart on the other side
NO_COUNTERPART = {
    # only after checking the whole inventory: these really have no role on the other side
    "android_only": ["Gallery", "SlidingDrawer", "DialerFilter", "ZoomButton", "ZoomControls"],
    "hmos_only": ["PatternLock", "QRCode", "Marquee", "DataPanel", "Component3D",
                  "DepthComponent", "ArcAlphabetIndexer", "ArcList", "ArcListItem"],
}
# Adapter/inflater plumbing on the Android side: not a visual element at all.
# Kept separate from the ArkUI side because names collide (android.widget.Filter is a
# filtering helper, the ArkUI advanced component `Filter` is a filter bar).
NON_VISUAL_ANDROID = {"Adapter", "AdapterView", "BaseAdapter", "CursorAdapter", "ListAdapter",
              "SpinnerAdapter", "ThemedSpinnerAdapter", "WrapperListAdapter",
              "HeaderViewListAdapter", "ResourceCursorAdapter", "ArrayAdapter",
              "SimpleAdapter", "SimpleCursorAdapter", "CursorTreeAdapter",
              "SimpleCursorTreeAdapter", "ResourceCursorTreeAdapter",
              "BaseExpandableListAdapter", "SimpleExpandableListAdapter",
              "ExpandableListAdapter", "Filter", "Filterable", "FilterQueryProvider",
              "SectionIndexer", "HeterogeneousExpandableList", "Advanceable", "Checkable",
              "RemoteViewsService", "ShareActionProvider", "OverScroller", "Scroller",
              "EdgeEffect", "ZoomButtonsController", "NavPushPathHelper", "Adapter",
              "AdapterView", "ListAdapter", "SpinnerAdapter", "ThemedSpinnerAdapter",
              "ResourceCursorAdapter", "Advanceable", "SectionIndexer",
              "HeterogeneousExpandableList", "Filterable", "Checkable", "ZoomButtonsController",
              "ShareActionProvider", "Label"}   # compose Label: internal material3 impl, not public
NON_VISUAL_HMOS: set[str] = {"NavPushPathHelper"}
NON_VISUAL = NON_VISUAL_ANDROID | NON_VISUAL_HMOS

ROLE_TITLES = {
    "toggle": "Switch / Toggle", "checkbox": "Checkbox", "checkbox_group": "Checkbox group",
    "radio": "Radio", "slider": "Slider", "progress": "Progress", "rating": "Rating",
    "input": "Text input", "search": "Search field", "picker_date": "Date picker",
    "picker_time": "Time picker", "picker_text": "Wheel picker", "stepper": "Stepper",
    "tabs": "Tabs", "appbar": "Top app bar", "navbar": "Navigation", "menu": "Menu",
    "dialog": "Dialog / bottom sheet", "toast": "Toast", "refresh": "Pull to refresh",
    "badge": "Badge", "divider": "Divider", "scrollbar": "Scrollbar", "swipe": "Swipe action",
    "button": "Button", "fab": "Floating action button", "icon_button": "Icon button",
    "chip": "Chip", "label": "Text", "image": "Image", "video": "Video", "web": "Web view",
    "card": "Card", "list": "List", "grid": "Grid", "pager": "Pager",
    "expandable": "Expandable list", "tooltip": "Tooltip", "clock": "Clock", "filter": "Filter",
    "segmented": "Segmented control", "chip_group": "Chip group", "list_item": "List item",
    "switcher": "Content switcher", "container": "Container (not compared)",
}


def main() -> int:
    hmos = set(ARKUI_BASIC + ARKUI_CONTAINER + ARKUI_MEDIA + ARKUI_EXTRA + ARKUI_ADVANCED
               + ARKUI_ATOMIC)
    android = set(ANDROID_WIDGET + ANDROID_MATERIAL + ANDROID_ANDROIDX + ANDROID_COMPOSE
                  + ANDROID_EXTRA)

    role_of_hmos: dict[str, str] = {}
    role_of_android: dict[str, str] = {}
    dupes: list[str] = []

    for role, spec in ROLES.items():
        for h in spec["hmos"]:
            if h in role_of_hmos and role_of_hmos[h] != role:
                dupes.append(f"hmos {h}: {role_of_hmos[h]} / {role}")
            role_of_hmos[h] = role
            if h not in hmos:
                print(f"! role {role}: unknown hmos component {h}")
        for a in spec["android"]:
            if a in role_of_android and role_of_android[a] != role:
                dupes.append(f"android {a}: {role_of_android[a]} / {role}")
            role_of_android[a] = role
            if a not in android:
                print(f"! role {role}: unknown android class {a}")

    unmapped_h = sorted(hmos - set(role_of_hmos) - set(NO_COUNTERPART["hmos_only"]) - NON_VISUAL)
    unmapped_a = sorted(android - set(role_of_android) - set(NO_COUNTERPART["android_only"])
                        - NON_VISUAL)

    print(f"hmos components   : {len(hmos)}  mapped {len(role_of_hmos)}  "
          f"hmos-only {len(NO_COUNTERPART['hmos_only'])}  non-visual "
          f"{len(NON_VISUAL & hmos)}  roles {len(ROLES)}")
    print(f"android classes   : {len(android)}  mapped {len(role_of_android)}  "
          f"android-only {len(NO_COUNTERPART['android_only'])}  non-visual "
          f"{len(NON_VISUAL & android)}")
    if unmapped_h:
        print("UNMAPPED hmos   :", unmapped_h)
    if unmapped_a:
        print("UNMAPPED android:", unmapped_a)
    if dupes:
        print("DUPLICATES:", dupes)

    # ── self checks: fail loudly instead of shipping a wrong table ──────────
    problems = []
    for role, spec in ROLES.items():
        bad_a = set(spec["android"]) & NON_VISUAL_ANDROID
        if bad_a:
            problems.append(f"{role}: non-visual android class {sorted(bad_a)}")
        bad_h = set(spec["hmos"]) & NON_VISUAL_HMOS
        if bad_h:
            problems.append(f"{role}: non-visual hmos component {sorted(bad_h)}")
        if len(spec["android"]) != len(set(spec["android"])):
            problems.append(f"{role}: duplicate android entry")
        if len(spec["hmos"]) != len(set(spec["hmos"])):
            problems.append(f"{role}: duplicate hmos entry")
        if not spec["android"] and spec.get("android_via", "component") == "component":
            problems.append(f"{role}: android side empty but android_via not declared")
        if not spec["hmos"] and spec.get("hmos_via", "component") == "component":
            problems.append(f"{role}: hmos side empty but hmos_via not declared")
    visuals = set(ROLES) - set()
    for role in ("container",):
        if ROLES[role]["check_style"]:
            problems.append(f"{role}: must be check_style=false")
    # a component cannot be both mapped and "no counterpart", and the lists must be real
    dup_a = set(NO_COUNTERPART["android_only"]) & set(role_of_android)
    dup_h = set(NO_COUNTERPART["hmos_only"]) & set(role_of_hmos)
    if dup_a:
        problems.append(f"android_only but also mapped: {sorted(dup_a)}")
    if dup_h:
        problems.append(f"hmos_only but also mapped: {sorted(dup_h)}")
    ghost_a = (set(NO_COUNTERPART["android_only"]) | set(NO_COUNTERPART["hmos_only"])) - android - hmos
    if ghost_a:
        problems.append(f"listed but not in any inventory: {sorted(ghost_a)}")

    missing_titles = [r for r in ROLES if r not in ROLE_TITLES]
    if missing_titles:
        problems.append(f"roles without a title: {missing_titles}")

    if problems:
        print("SELF-CHECK FAILED:")
        for x in problems:
            print("  -", x)
        return 1

    out = {
        "meta": {
            "version": 1,
            "sources": {
                "hmos": "openharmony/docs master zh-cn/application-dev/reference/apis-arkui/arkui-ts "
                        "(51 basic + 45 containers)",
                "android_widget": "android-36 android.jar -> android/widget",
                "android_material": "com.google.android.material",
                "android_compose": "androidx.compose.material3 runtime jar (as in sunflower)",
            },
            "check_style_false": "D1: platform owns the look; compare existence/position/state only",
            "check_style_true": "D2: appearance is app-authored; compare it",
        },
        "roles": {r: {"check_style": s["check_style"], "state": s["state"],
                      "hmos_via": s.get("hmos_via", "component"),
                      "android_via": s.get("android_via", "component")}
                  for r, s in ROLES.items()},
        "android": {a: r for a, r in sorted(role_of_android.items())},
        "hmos": {h: r for h, r in sorted(role_of_hmos.items())},
        "no_counterpart": NO_COUNTERPART,
        "non_visual": sorted(NON_VISUAL),
    }
    root = Path(__file__).resolve().parents[1]
    (root / "tools" / "role_map.yaml").write_text(
        yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # markdown table
    lines = ["# Android <-> ArkUI component role map", "",
             f"Sources: {len(hmos)} ArkUI components (OpenHarmony master docs), "
             f"{len(android)} Android classes (android-36 widget + Material + Compose M3).", "",
             "`compare appearance = no` is D1: the platform owns the look, so only "
             "existence / position / state are checked.", "",
             "| Role | Android | ArkUI | Compare appearance | State field |",
             "|---|---|---|---|---|"]
    for role, spec in ROLES.items():
        a = ", ".join(f"`{x}`" for x in spec["android"]) or "—"
        h = ", ".join(f"`{x}`" for x in spec["hmos"]) or "—"
        lines.append(f"| {ROLE_TITLES.get(role, role)} `{role}` | {a} | {h} | "
                     f"{'**yes**' if spec['check_style'] else 'no'} | {spec['state'] or '—'} |")
    lines += ["", "## No counterpart", "",
              f"- Android only: {', '.join(f'`{x}`' for x in NO_COUNTERPART['android_only'])}",
              f"- ArkUI only: {', '.join(f'`{x}`' for x in NO_COUNTERPART['hmos_only'])}"]
    (root / "docs" / "role-map.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("wrote tools/role_map.yaml, docs/role-map.md")
    return 0 if not (unmapped_h or unmapped_a) else 1


if __name__ == "__main__":
    sys.exit(main())
