from typing import List, Literal, Optional, Set

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class NoteText(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def reject_whitespace_only_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Note text must contain non-whitespace characters")
        return value


class NoteCreate(NoteText):
    pass


class NoteUpdate(NoteText):
    pass


class Note(BaseModel):
    id: int
    text: str
    created_at: str


class InboxItemText(BaseModel):
    raw_text: str = Field(min_length=1, max_length=2000)

    @field_validator("raw_text")
    @classmethod
    def reject_whitespace_only_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Item text must contain non-whitespace characters")
        return value


class InboxItemCreate(InboxItemText):
    pass


class InboxItemUpdate(InboxItemText):
    pass


class InboxItem(BaseModel):
    id: int
    item_type: str
    title: Optional[str]
    raw_text: Optional[str]
    source_url: Optional[str]
    original_filename: Optional[str]
    mime_type: Optional[str]
    processing_status: str
    error_message: Optional[str] = None
    storage_path: Optional[str] = None
    extracted_text: Optional[str] = None
    ai_description: Optional[str] = None
    owner_user_id: Optional[int] = None
    created_at: str
    updated_at: Optional[str]


class UrlItemCreate(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ExtractedTextUpdate(BaseModel):
    extracted_text: str = Field(min_length=0, max_length=20000)


Priority = Literal["High", "Medium", "Low"]
AnalysisMode = Literal[
    "openai",
    "fallback_no_api_call",
    "fallback_after_api_error",
    "empty_inbox",
]


class StrictAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisNote(StrictAnalysisModel):
    id: int
    title: str = Field(min_length=1)
    priority: Priority
    tags: List[str] = Field(default_factory=list)
    rationale: Optional[str] = None


class AnalysisBucket(StrictAnalysisModel):
    name: str = Field(min_length=1)
    notes: List[AnalysisNote]


class AnalysisTodo(StrictAnalysisModel):
    task: str = Field(min_length=1)
    priority: Priority
    source_note_ids: List[int] = Field(min_length=1)
    when: Optional[str] = None


class AnalysisPriorityItem(StrictAnalysisModel):
    id: int
    title: str = Field(min_length=1)


class AnalysisPriorityView(StrictAnalysisModel):
    High: List[AnalysisPriorityItem]
    Medium: List[AnalysisPriorityItem]
    Low: List[AnalysisPriorityItem]


class AnalysisResult(StrictAnalysisModel):
    buckets: List[AnalysisBucket]
    todos: List[AnalysisTodo]
    priority_view: AnalysisPriorityView
    mode: AnalysisMode

    @model_validator(mode="after")
    def validate_consistent_item_views(self):
        bucket_ids = [
            note.id
            for bucket in self.buckets
            for note in bucket.notes
        ]
        priority_ids = [
            item.id
            for level in ("High", "Medium", "Low")
            for item in getattr(self.priority_view, level)
        ]
        if len(bucket_ids) != len(set(bucket_ids)):
            raise ValueError("Each item must appear in exactly one bucket")
        if len(priority_ids) != len(set(priority_ids)):
            raise ValueError(
                "Each item must appear once in the priority view"
            )
        if set(bucket_ids) != set(priority_ids):
            raise ValueError(
                "Bucket and priority views must contain the same items"
            )
        return self

    def validate_for_item_ids(self, expected_ids: Set[int]) -> None:
        bucket_ids = {
            note.id
            for bucket in self.buckets
            for note in bucket.notes
        }
        if bucket_ids != expected_ids:
            raise ValueError(
                "Analysis item IDs must match the current inbox"
            )
        todo_source_ids = {
            item_id
            for todo in self.todos
            for item_id in todo.source_note_ids
        }
        if not todo_source_ids <= expected_ids:
            raise ValueError("ToDos reference unknown inbox items")


class PersistedAnalysis(AnalysisResult):
    analysis_run_id: int
    generated_at: str
    is_stale: bool = False
    inbox_fingerprint: Optional[str] = None


TaskStatus = Literal["open", "completed", "dismissed"]
TaskListStatus = Literal["open", "completed", "dismissed", "all"]


class Task(BaseModel):
    id: int
    text: str
    priority: Priority
    timing_text: Optional[str]
    ai_timing_text: Optional[str] = None
    timing_confirmed: bool = False
    due_on: Optional[str] = None
    due_confidence: Optional[str] = None
    snoozed_until: Optional[str] = None
    reminder_at: Optional[str] = None
    status: TaskStatus
    source_item_ids: List[int]
    created_at: str
    updated_at: str


class TaskTimingConfirm(BaseModel):
    timing_text: Optional[str] = Field(default=None, max_length=200)


class TaskSnoozeRequest(BaseModel):
    until: str = Field(min_length=8, max_length=32)


class TaskReminderRequest(BaseModel):
    reminder_at: str = Field(min_length=8, max_length=32)


class CalendarPreviewRequest(BaseModel):
    task_id: int
    title: Optional[str] = Field(default=None, max_length=200)
    start_date: Optional[str] = Field(default=None, max_length=32)
    description: Optional[str] = Field(default=None, max_length=2000)


class CalendarConfirmRequest(BaseModel):
    draft_id: str = Field(min_length=4, max_length=80)


class TopicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=400)


class TopicUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=400)
    archived: Optional[bool] = None


class Topic(BaseModel):
    id: int
    name: str
    description: Optional[str]
    archived: bool
    item_count: int = 0
    created_at: str
    updated_at: str


class InsightOverrideUpdate(BaseModel):
    user_title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    user_topic_id: Optional[int] = None
    user_priority: Optional[Priority] = None
    user_tags: Optional[List[str]] = None
    clear_overrides: bool = False
    needs_review: Optional[bool] = None


class ItemInsight(BaseModel):
    item_id: int
    analysis_run_id: int
    category: str
    concise_title: str
    priority: Priority
    topic_id: Optional[int]
    tags: List[str]
    rationale: Optional[str]
    user_title: Optional[str]
    user_topic_id: Optional[int]
    user_priority: Optional[Priority]
    user_tags: Optional[List[str]]
    effective_title: str
    effective_priority: Priority
    effective_topic_id: Optional[int]
    effective_tags: List[str]
    needs_review: bool
    updated_at: str
    has_user_overrides: bool


class ItemRelation(BaseModel):
    id: int
    item_id_a: int
    item_id_b: int
    relation_type: str
    score: float
    dismissed: bool
    created_at: str


class SearchHit(BaseModel):
    item_id: int
    item_type: str
    title: str
    excerpt: str
    source_kind: str
    preview_url: Optional[str] = None
    score: float
    keyword_score: float
    semantic_score: float


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    item_type: Optional[str] = Field(default=None, max_length=100)


class AskCitation(BaseModel):
    id: str
    item_id: int
    item_type: str
    title: str
    excerpt: str
    source_kind: str
    preview_url: Optional[str] = None
    score: float


class AskResponse(BaseModel):
    question: str
    answer: str
    confidence: Literal["high", "medium", "insufficient"]
    citations: List[AskCitation]
    answer_kind: Literal["grounded_summary", "model_generated"]
    mode: Literal["llm_grounded", "extractive_fallback"] = "extractive_fallback"
