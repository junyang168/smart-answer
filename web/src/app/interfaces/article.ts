export interface Article {
    id: string;
    publishedUrl: string;
    theme: string;
    title: string;
    snippet: string;
    deliver_date: string;
    status: string;
    assigned_to_name: string;
//    duration: string;    
    thumbnail: {
      url: string;
      width: number;
      height: number;
      imageId: string;
    };

  }
