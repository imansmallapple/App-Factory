# Android ↔ ArkUI 组件角色映射

来源：鸿蒙 135 个 ArkUI 组件（OpenHarmony master 文档），安卓 190 个类（android-36 widget + Material + Compose M3）。

`外观比不比 = 否` 即 D1：平台拥有外观，只查存在/位置/状态。

| 角色 | Android | ArkUI | 外观比不比 | 状态字段 |
|---|---|---|---|---|
| 开关 `toggle` | `Switch`, `SwitchMaterial`, `MaterialSwitch`, `ToggleButton`, `CompoundButton` | `Toggle` | 否 | checked |
| 复选框 `checkbox` | `CheckBox`, `Checkbox` | `Checkbox` | 否 | checked |
| 复选框组 `checkbox_group` | — | `CheckboxGroup` | 否 | checked |
| 单选 `radio` | `RadioButton`, `RadioGroup` | `Radio` | 否 | checked |
| 滑块 `slider` | `SeekBar`, `AbsSeekBar`, `Slider`, `RangeSlider` | `Slider`, `ArcSlider` | 否 | value |
| 进度 `progress` | `ProgressBar`, `CircularProgressIndicator`, `LinearProgressIndicator` | `Progress`, `LoadingProgress`, `Gauge` | 否 | value |
| 评分 `rating` | `RatingBar` | `Rating` | 否 | value |
| 文本输入 `input` | `EditText`, `TextInputEditText`, `TextInputLayout`, `AutoCompleteTextView`, `MultiAutoCompleteTextView`, `TextField`, `OutlinedTextField` | `TextInput`, `TextArea`, `RichEditor` | 否 | text |
| 搜索框 `search` | `SearchView`, `SearchBar` | `Search`, `AtomicServiceSearch` | 否 | text |
| 日期选择 `picker_date` | `DatePicker`, `CalendarView`, `DateRangePicker` | `DatePicker`, `CalendarPicker` | 否 | text |
| 时间选择 `picker_time` | `TimePicker` | `TimePicker` | 否 | text |
| 滚轮文本选择 `picker_text` | `Spinner`, `NumberPicker`, `AbsSpinner` | `TextPicker`, `Select` | 否 | text |
| 步进器 `stepper` | — | `Stepper`, `StepperItem`, `Counter`, `CounterV2` | 否 | text |
| 标签页 `tabs` | `TabHost`, `TabWidget`, `TabLayout`, `TabRow`, `Tab` | `Tabs`, `TabContent`, `AtomicServiceTabs` | 否 | selected |
| 顶部栏 `appbar` | `Toolbar`, `MaterialToolbar`, `ActionMenuView`, `AppBarLayout`, `CollapsingToolbarLayout`, `TopAppBar`, `CenterAlignedTopAppBar`, `AppBar` | `Navigation`, `ToolbarItem`, `ComposeTitleBar`, `SelectTitleBar`, `EditableTitleBar`, `TabTitleBar`, `ToolBar`, `ToolBarV2`, `SubHeader`, `SubHeaderV2` | 否 | — |
| 导航 `navbar` | `BottomNavigationView`, `NavigationView`, `NavigationBar`, `NavigationRail`, `NavigationDrawer` | `Navigator`, `NavRouter`, `SideBarContainer`, `MultiNavigation`, `AtomicServiceNavigation` | 否 | selected |
| 菜单 `menu` | `PopupMenu`, `ListPopupWindow`, `DropdownMenu`, `ExposedDropdownMenuBox`, `Menu` | `Menu`, `MenuItem`, `MenuItemGroup`, `SelectionMenu`, `formmenu`, `Popup`, `PopupV2` | 否 | selected |
| 筛选器 `filter` | — | `Filter` | 否 | — |
| 弹层 `dialog` | `BottomSheetDialog`, `BottomSheetDialogFragment`, `MaterialAlertDialogBuilder`, `AlertDialog`, `BottomSheetScaffold`, `ModalBottomSheet`, `Dialog`, `DialogFragment`, `MaterialDatePicker`, `DatePickerDialog`, `TimePickerDialog`, `PopupWindow` | `Panel`, `Dialog`, `DialogV2`, `ExceptionPrompt`, `InterstitialDialogAction` | 否 | — |
| 轻提示 `toast` | `Toast`, `Snackbar` | — | 否 | — |
| 下拉刷新 `refresh` | `SwipeRefreshLayout`, `PullToRefreshBox` | `Refresh`, `SwipeRefresher` | 否 | — |
| 角标 `badge` | `Badge`, `BadgeDrawable` | `Badge` | 否 | text |
| 分割线 `divider` | `Divider`, `HorizontalDivider`, `VerticalDivider` | `Divider` | 否 | — |
| 滚动条 `scrollbar` | — | `ScrollBar`, `ArcScrollBar` | 否 | — |
| 滑动容器 `swipe` | `SwipeToDismissBox` | — | 否 | — |
| 按钮 `button` | `Button`, `MaterialButton`, `OutlinedButton`, `TextButton`, `ElevatedButton`, `FilledTonalButton` | `Button`, `ArcButton`, `ProgressButton`, `ProgressButtonV2`, `DownloadFileButton` | **比** | text |
| 悬浮按钮 `fab` | `FloatingActionButton`, `ExtendedFloatingActionButton` | — | **比** | — |
| 图标按钮 `icon_button` | `ImageButton`, `IconButton`, `IconToggleButton`, `Icon` | `SymbolGlyph` | **比** | — |
| 标签块 `chip` | `Chip`, `FilterChip`, `AssistChip`, `InputChip`, `SuggestionChip` | `Chip` | **比** | text |
| 标签组 `chip_group` | `ChipGroup` | `ChipGroup` | **比** | selected |
| 分段控件 `segmented` | `SegmentedButton` | `SegmentButton`, `SegmentButtonV2` | 否 | selected |
| 列表项 `list_item` | `ListItem`, `CheckedTextView` | `ComposeListItem`, `ListItem` | **比** | — |
| 文本 `label` | `TextView`, `Text`, `TwoLineListItem` | `Text`, `Span`, `ContainerSpan`, `SymbolSpan` | **比** | text |
| 图片 `image` | `ImageView`, `QuickContactBadge`, `Image`, `Magnifier` | `Image`, `ImageSpan`, `ImageAnimator` | **比** | — |
| 视频 `video` | `VideoView`, `MediaController`, `SurfaceView`, `TextureView`, `GLSurfaceView` | `Video`, `XComponent` | **比** | — |
| 网页 `web` | `WebView` | `Web`, `RichText`, `AtomicServiceWeb` | **比** | — |
| 卡片 `card` | `CardView`, `MaterialCardView`, `Card`, `Surface`, `Scaffold` | — | **比** | — |
| 列表 `list` | `ListView`, `AbsListView`, `RecyclerView`, `LazyColumn`, `LazyRow` | `List`, `ListItemGroup` | **比** | — |
| 网格 `grid` | `GridView`, `GridLayout`, `LazyVerticalGrid` | `Grid`, `GridItem`, `GridRow`, `GridCol`, `WaterFlow`, `FlowItem`, `LazyVGridLayout`, `LazyVWaterFlowLayout`, `GridObjectSortComponent` | **比** | — |
| 翻页 `pager` | `ViewPager`, `ViewPager2`, `AdapterViewAnimator`, `AdapterViewFlipper`, `ViewFlipper`, `StackView` | `Swiper`, `ArcSwiper` | **比** | selected |
| 可展开列表 `expandable` | `ExpandableListView` | `TreeView` | **比** | — |
| 提示气泡 `tooltip` | `Tooltip` | — | **比** | — |
| 时钟 `clock` | `AnalogClock`, `TextClock`, `DigitalClock`, `Chronometer` | `TextClock`, `TextTimer` | **比** | text |
| 内容切换 `switcher` | `TextSwitcher`, `ViewSwitcher`, `ImageSwitcher` | — | **比** | — |
| 容器(不参与比对) `container` | `FrameLayout`, `LinearLayout`, `RelativeLayout`, `AbsoluteLayout`, `TableLayout`, `TableRow`, `Space`, `ScrollView`, `HorizontalScrollView`, `RemoteViews`, `ViewAnimator`, `AlphabetIndexer` | `Column`, `Row`, `ColumnSplit`, `RowSplit`, `Stack`, `Flex`, `Blank`, `RelativeContainer`, `Scroll`, `DynamicLayout`, `FolderStack`, `NodeContainer`, `EmbeddedComponent`, `ContainerReader`, `FormLink`, `Hyperlink`, `GridContainer`, `LazyColumnLayout`, `AlphabetIndexer`, `NavDestination`, `FoldSplitContainer`, `SplitLayout`, `FullScreenLaunchComponent`, `HalfScreenLaunchComponent` | 否 | — |

## 无对应项

- 仅 Android：`Gallery`, `SlidingDrawer`, `DialerFilter`, `ZoomButton`, `ZoomControls`
- 仅 ArkUI：`PatternLock`, `QRCode`, `Marquee`, `DataPanel`, `Component3D`, `DepthComponent`, `ArcAlphabetIndexer`, `ArcList`, `ArcListItem`
