// util/bible-order.ts

export const BIBLE_BOOKS_CANONICAL_ORDER: string[] = [
    // 舊約 (Old Testament)
    "創世記", "出埃及記", "利未記", "民數記", "申命記",
    "約書亞記", "士師記", "路得記",
    "撒母耳記上", "撒母耳記下", "列王紀上", "列王紀下",
    "歷代志上", "歷代志下", "以斯拉記", "尼希米記", "以斯帖記",
    "約伯記", "詩篇", "箴言", "傳道書", "雅歌",
    "以賽亞書", "耶利米書", "耶利米哀歌", "以西結書", "但以理書",
    "何西阿書", "約珥書", "阿摩司書", "俄巴底亞書", "約拿書",
    "彌迦書", "那鴻書", "哈巴谷書", "西番雅書", "哈該書",
    "撒迦利亞書", "瑪拉基書",

    // 新約 (New Testament)
    "馬太福音", "馬可福音", "路加福音", "約翰福音",
    "使徒行傳",
    "羅馬書", "哥林多前書", "哥林多後書", "加拉太書", "以弗所書",
    "腓立比書", "歌羅西書",
    "帖撒羅尼迦前書", "帖撒羅尼迦後書",
    "提摩太前書", "提摩太後書", "提多書", "腓利門書",
    "希伯來書",
    "雅各書", "彼得前書", "彼得後書",
    "約翰一書", "約翰二書", "約翰三書", "猶大書",
    "啟示錄"
];

// 創建一個查找映射以提高性能
const bookOrderMap = new Map<string, number>();
BIBLE_BOOKS_CANONICAL_ORDER.forEach((book, index) => {
    bookOrderMap.set(book, index);
});

// 導出一個函數，用於獲取書卷的排序索引
export const getBookOrderIndex = (bookName: string): number => {
    // 為了更健壯，在查找前先去除可能的空格
    const trimmedBookName = bookName.trim();
    return bookOrderMap.get(trimmedBookName) ?? Infinity; // 如果找不到，返回 Infinity，使其排在最後
};