# Android <-> ArkUI component role map

Sources: 135 ArkUI components (OpenHarmony master docs), 190 Android classes (android-36 widget + Material + Compose M3).

`compare appearance = no` is D1: the platform owns the look, so only existence / position / state are checked.

| Role | Android | ArkUI | Compare appearance | State field |
|---|---|---|---|---|
| Switch / Toggle `toggle` | `Switch`, `SwitchMaterial`, `MaterialSwitch`, `ToggleButton`, `CompoundButton` | `Toggle` | no | checked |
| Checkbox `checkbox` | `CheckBox`, `Checkbox` | `Checkbox` | no | checked |
| Checkbox group `checkbox_group` | — | `CheckboxGroup` | no | checked |
| Radio `radio` | `RadioButton`, `RadioGroup` | `Radio` | no | checked |
| Slider `slider` | `SeekBar`, `AbsSeekBar`, `Slider`, `RangeSlider` | `Slider`, `ArcSlider` | no | value |
| Progress `progress` | `ProgressBar`, `CircularProgressIndicator`, `LinearProgressIndicator` | `Progress`, `LoadingProgress`, `Gauge` | no | value |
| Rating `rating` | `RatingBar` | `Rating` | no | value |
| Text input `input` | `EditText`, `TextInputEditText`, `TextInputLayout`, `AutoCompleteTextView`, `MultiAutoCompleteTextView`, `TextField`, `OutlinedTextField` | `TextInput`, `TextArea`, `RichEditor` | no | text |
| Search field `search` | `SearchView`, `SearchBar` | `Search`, `AtomicServiceSearch` | no | text |
| Date picker `picker_date` | `DatePicker`, `CalendarView`, `DateRangePicker` | `DatePicker`, `CalendarPicker` | no | text |
| Time picker `picker_time` | `TimePicker` | `TimePicker` | no | text |
| Wheel picker `picker_text` | `Spinner`, `NumberPicker`, `AbsSpinner` | `TextPicker`, `Select` | no | text |
| Stepper `stepper` | — | `Stepper`, `StepperItem`, `Counter`, `CounterV2` | no | text |
| Tabs `tabs` | `TabHost`, `TabWidget`, `TabLayout`, `TabRow`, `Tab` | `Tabs`, `TabContent`, `AtomicServiceTabs` | no | selected |
| Top app bar `appbar` | `Toolbar`, `MaterialToolbar`, `ActionMenuView`, `AppBarLayout`, `CollapsingToolbarLayout`, `TopAppBar`, `CenterAlignedTopAppBar`, `AppBar` | `Navigation`, `ToolbarItem`, `ComposeTitleBar`, `SelectTitleBar`, `EditableTitleBar`, `TabTitleBar`, `ToolBar`, `ToolBarV2`, `SubHeader`, `SubHeaderV2` | no | — |
| Navigation `navbar` | `BottomNavigationView`, `NavigationView`, `NavigationBar`, `NavigationRail`, `NavigationDrawer` | `Navigator`, `NavRouter`, `SideBarContainer`, `MultiNavigation`, `AtomicServiceNavigation` | no | selected |
| Menu `menu` | `PopupMenu`, `ListPopupWindow`, `DropdownMenu`, `ExposedDropdownMenuBox`, `Menu` | `Menu`, `MenuItem`, `MenuItemGroup`, `SelectionMenu`, `formmenu`, `Popup`, `PopupV2` | no | selected |
| Filter `filter` | — | `Filter` | no | — |
| Dialog / bottom sheet `dialog` | `BottomSheetDialog`, `BottomSheetDialogFragment`, `MaterialAlertDialogBuilder`, `AlertDialog`, `BottomSheetScaffold`, `ModalBottomSheet`, `Dialog`, `DialogFragment`, `MaterialDatePicker`, `DatePickerDialog`, `TimePickerDialog`, `PopupWindow` | `Panel`, `Dialog`, `DialogV2`, `ExceptionPrompt`, `InterstitialDialogAction` | no | — |
| Toast `toast` | `Toast`, `Snackbar` | — | no | — |
| Pull to refresh `refresh` | `SwipeRefreshLayout`, `PullToRefreshBox` | `Refresh`, `SwipeRefresher` | no | — |
| Badge `badge` | `Badge`, `BadgeDrawable` | `Badge` | no | text |
| Divider `divider` | `Divider`, `HorizontalDivider`, `VerticalDivider` | `Divider` | no | — |
| Scrollbar `scrollbar` | — | `ScrollBar`, `ArcScrollBar` | no | — |
| Swipe action `swipe` | `SwipeToDismissBox` | — | no | — |
| Button `button` | `Button`, `MaterialButton`, `OutlinedButton`, `TextButton`, `ElevatedButton`, `FilledTonalButton` | `Button`, `ArcButton`, `ProgressButton`, `ProgressButtonV2`, `DownloadFileButton` | **yes** | text |
| Floating action button `fab` | `FloatingActionButton`, `ExtendedFloatingActionButton` | — | **yes** | — |
| Icon button `icon_button` | `ImageButton`, `IconButton`, `IconToggleButton`, `Icon` | `SymbolGlyph` | **yes** | — |
| Chip `chip` | `Chip`, `FilterChip`, `AssistChip`, `InputChip`, `SuggestionChip` | `Chip` | **yes** | text |
| Chip group `chip_group` | `ChipGroup` | `ChipGroup` | **yes** | selected |
| Segmented control `segmented` | `SegmentedButton` | `SegmentButton`, `SegmentButtonV2` | no | selected |
| List item `list_item` | `ListItem`, `CheckedTextView` | `ComposeListItem`, `ListItem` | **yes** | — |
| Text `label` | `TextView`, `Text`, `TwoLineListItem` | `Text`, `Span`, `ContainerSpan`, `SymbolSpan` | **yes** | text |
| Image `image` | `ImageView`, `QuickContactBadge`, `Image`, `Magnifier` | `Image`, `ImageSpan`, `ImageAnimator` | **yes** | — |
| Video `video` | `VideoView`, `MediaController`, `SurfaceView`, `TextureView`, `GLSurfaceView` | `Video`, `XComponent` | **yes** | — |
| Web view `web` | `WebView` | `Web`, `RichText`, `AtomicServiceWeb` | **yes** | — |
| Card `card` | `CardView`, `MaterialCardView`, `Card`, `Surface`, `Scaffold` | — | **yes** | — |
| List `list` | `ListView`, `AbsListView`, `RecyclerView`, `LazyColumn`, `LazyRow` | `List`, `ListItemGroup` | **yes** | — |
| Grid `grid` | `GridView`, `GridLayout`, `LazyVerticalGrid` | `Grid`, `GridItem`, `GridRow`, `GridCol`, `WaterFlow`, `FlowItem`, `LazyVGridLayout`, `LazyVWaterFlowLayout`, `GridObjectSortComponent` | **yes** | — |
| Pager `pager` | `ViewPager`, `ViewPager2`, `AdapterViewAnimator`, `AdapterViewFlipper`, `ViewFlipper`, `StackView` | `Swiper`, `ArcSwiper` | **yes** | selected |
| Expandable list `expandable` | `ExpandableListView` | `TreeView` | **yes** | — |
| Tooltip `tooltip` | `Tooltip` | — | **yes** | — |
| Clock `clock` | `AnalogClock`, `TextClock`, `DigitalClock`, `Chronometer` | `TextClock`, `TextTimer` | **yes** | text |
| Content switcher `switcher` | `TextSwitcher`, `ViewSwitcher`, `ImageSwitcher` | — | **yes** | — |
| Container (not compared) `container` | `FrameLayout`, `LinearLayout`, `RelativeLayout`, `AbsoluteLayout`, `TableLayout`, `TableRow`, `Space`, `ScrollView`, `HorizontalScrollView`, `RemoteViews`, `ViewAnimator`, `AlphabetIndexer` | `Column`, `Row`, `ColumnSplit`, `RowSplit`, `Stack`, `Flex`, `Blank`, `RelativeContainer`, `Scroll`, `DynamicLayout`, `FolderStack`, `NodeContainer`, `EmbeddedComponent`, `ContainerReader`, `FormLink`, `Hyperlink`, `GridContainer`, `LazyColumnLayout`, `AlphabetIndexer`, `NavDestination`, `FoldSplitContainer`, `SplitLayout`, `FullScreenLaunchComponent`, `HalfScreenLaunchComponent` | no | — |

## No counterpart

- Android only: `Gallery`, `SlidingDrawer`, `DialerFilter`, `ZoomButton`, `ZoomControls`
- ArkUI only: `PatternLock`, `QRCode`, `Marquee`, `DataPanel`, `Component3D`, `DepthComponent`, `ArcAlphabetIndexer`, `ArcList`, `ArcListItem`
